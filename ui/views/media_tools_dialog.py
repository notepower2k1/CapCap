#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui/views/media_tools_dialog.py

Media Tools Dialog for CapCap:
1. Split / Trim Video: Segment video by duration, equal parts, or exact timestamp range.
2. Merge Videos: Concatenate multiple video clips (stream copy or standard 1080p re-encode).
3. Separate Audio / Video: Extract audio track (.mp3, .wav, .m4a) and/or muted video.
4. Mux Audio + Video: Combine video with separate audio track with optional stream trimming.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

try:
    from i18n import localize_widget_tree, t
except ImportError:
    from ui.i18n import localize_widget_tree, t

try:
    from runtime_paths import bin_path, subprocess_hidden_kwargs
except ImportError:
    from app.runtime_paths import bin_path, subprocess_hidden_kwargs

try:
    from ui.widgets.spin_boxes import NoWheelSpinBox
except ImportError:
    try:
        from widgets.spin_boxes import NoWheelSpinBox
    except ImportError:
        class NoWheelSpinBox(QSpinBox):
            def wheelEvent(self, event):
                event.ignore()


def _ffmpeg_path() -> str:
    """Resolve bundled ffmpeg executable or fallback to PATH."""
    try:
        from runtime_paths import bin_path
        bundled = os.path.join(bin_path(), "ffmpeg", "ffmpeg.exe")
        if os.path.exists(bundled):
            return bundled
    except Exception:
        pass
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


def _get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using PyAV metadata first (0 subprocesses)."""
    if not video_path or not os.path.exists(video_path):
        return 0.0
    try:
        import av
        with av.open(video_path) as container:
            if container.duration is not None and container.duration > 0:
                return float(container.duration / 1_000_000.0)
            for stream in container.streams:
                if stream.duration is not None and stream.duration > 0 and stream.time_base is not None:
                    return float(stream.duration * stream.time_base)
    except Exception:
        pass

    # Fallback to ffprobe
    try:
        ffprobe_bin = shutil.which("ffprobe")
        if not ffprobe_bin:
            try:
                from runtime_paths import bin_path
                ffprobe_bin = os.path.join(bin_path(), "ffmpeg", "ffprobe.exe")
            except Exception:
                ffprobe_bin = ""
        if ffprobe_bin and os.path.exists(ffprobe_bin):
            res = subprocess.run(
                [ffprobe_bin, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video_path],
                capture_output=True, text=True, timeout=15, **subprocess_hidden_kwargs(),
            )
            val = float(res.stdout.strip())
            if val > 0:
                return val
    except Exception:
        pass
    return 0.0


# =========================================================================
# Pure Helper Functions (Command builders and formatters)
# =========================================================================

def format_hms(seconds: float) -> str:
    """Format non-negative seconds into HH:MM:SS string."""
    try:
        s = max(0, int(round(float(seconds or 0.0))))
    except (TypeError, ValueError):
        s = 0
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


def parse_hms(text: str) -> float:
    """Parse HH:MM:SS, MM:SS, or SS into total float seconds. Return -1.0 if invalid."""
    if not text or not isinstance(text, str):
        return -1.0
    clean = text.strip()
    if not clean:
        return -1.0
    parts = clean.split(":")
    if len(parts) > 3 or len(parts) == 0:
        return -1.0
    try:
        nums = [float(p.strip()) for p in parts]
        if any(n < 0.0 or math.isnan(n) or math.isinf(n) for n in nums):
            return -1.0
        if len(nums) == 3:
            return nums[0] * 3600.0 + nums[1] * 60.0 + nums[2]
        elif len(nums) == 2:
            return nums[0] * 60.0 + nums[1]
        else:
            return nums[0]
    except (TypeError, ValueError):
        return -1.0


def build_split_ffmpeg_args(
    input_path: str,
    output_dir: str,
    mode: str,
    *,
    segment_seconds: float = 0.0,
    num_parts: int = 2,
    total_duration: float = 0.0,
    trim_start_s: float = 0.0,
    trim_end_s: float = 0.0,
) -> tuple[list[str], str]:
    """Build FFmpeg arguments for splitting or trimming video."""
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    ext = os.path.splitext(input_path)[1] or ".mp4"

    if mode in ("duration", "parts"):
        if mode == "parts":
            parts_cnt = max(1, int(num_parts))
            if total_duration > 0:
                seg_s = max(1.0, math.ceil(float(total_duration) / parts_cnt))
            else:
                seg_s = max(1.0, float(segment_seconds or 60.0))
        else:
            seg_s = max(1.0, float(segment_seconds))

        out_pattern = os.path.join(output_dir, f"{base_name}_part%03d{ext}")
        args = [
            "-i", input_path,
            "-c", "copy",
            "-f", "segment",
            "-segment_time", f"{seg_s:.3f}",
            "-reset_timestamps", "1",
            out_pattern,
        ]
        return args, output_dir

    elif mode == "trim":
        start_tag = format_hms(trim_start_s).replace(":", "")
        end_tag = format_hms(trim_end_s).replace(":", "")
        out_file = os.path.join(output_dir, f"{base_name}_trim_{start_tag}_{end_tag}{ext}")
        args = [
            "-ss", f"{trim_start_s:.3f}",
            "-to", f"{trim_end_s:.3f}",
            "-i", input_path,
            "-c", "copy",
            "-avoid_negative_ts", "make_zero",
            out_file,
        ]
        return args, out_file

    raise ValueError(f"Unknown split mode: {mode}")


def write_concat_list_file(video_paths: list[str], list_file_path: str) -> None:
    """Write FFmpeg concat demuxer text file in UTF-8."""
    lines = []
    for p in video_paths:
        abs_p = os.path.abspath(p).replace("\\", "/")
        escaped = abs_p.replace("'", "'\\''")
        lines.append(f"file '{escaped}'\n")
    with open(list_file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def build_merge_ffmpeg_args(
    video_paths: list[str],
    output_file: str,
    list_file_path: str,
    *,
    reencode: bool = False,
) -> list[str]:
    """Build FFmpeg arguments for concatenating videos using concat demuxer."""
    if not reencode:
        return [
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-c", "copy",
            output_file,
        ]
    else:
        return [
            "-f", "concat",
            "-safe", "0",
            "-i", list_file_path,
            "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "44100",
            "-ac", "2",
            output_file,
        ]


def build_extract_ffmpeg_tasks(
    input_path: str,
    output_dir: str,
    extract_mode: str,
    audio_format: str,
) -> list[tuple[str, list[str], str]]:
    """Build list of (task_label, ffmpeg_args, output_target) for extraction."""
    base_name = os.path.splitext(os.path.basename(input_path))[0]
    v_ext = os.path.splitext(input_path)[1] or ".mp4"
    fmt = audio_format.lower().strip(".")
    if fmt not in ("mp3", "wav", "m4a"):
        fmt = "mp3"

    tasks: list[tuple[str, list[str], str]] = []

    if extract_mode in ("both", "audio_only"):
        out_audio = os.path.join(output_dir, f"{base_name}_audio.{fmt}")
        if fmt == "mp3":
            args = ["-i", input_path, "-vn", "-c:a", "libmp3lame", "-q:a", "2", out_audio]
        elif fmt == "wav":
            args = ["-i", input_path, "-vn", "-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2", out_audio]
        else:  # m4a
            args = ["-i", input_path, "-vn", "-c:a", "aac", "-b:a", "192k", out_audio]
        tasks.append((f"Extract Audio ({fmt.upper()})", args, out_audio))

    if extract_mode in ("both", "video_only"):
        out_video = os.path.join(output_dir, f"{base_name}_no_audio{v_ext}")
        args = ["-i", input_path, "-c:v", "copy", "-an", out_video]
        tasks.append(("Extract Video (Muted)", args, out_video))

    return tasks


def build_mux_ffmpeg_args(
    video_path: str,
    audio_path: str,
    output_file: str,
    *,
    shortest: bool = False,
) -> list[str]:
    """Build FFmpeg arguments for combining video stream with new audio stream."""
    args = [
        "-i", video_path,
        "-i", audio_path,
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
    ]
    if shortest:
        args.append("-shortest")
    args.append(output_file)
    return args


# =========================================================================
# Background Worker Thread
# =========================================================================

class _MediaToolWorker(QThread):
    """Background worker executing sequential FFmpeg tasks safely off the GUI thread."""

    progressText = Signal(str)
    finishedTask = Signal(bool, str, str)  # success, message, output_location

    def __init__(
        self,
        tasks: list[tuple[str, list[str], str]],
        temp_files: list[str] | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._tasks = tasks
        self._temp_files = temp_files or []
        self._process: subprocess.Popen | None = None
        self._is_cancelled = False

    def cancel(self) -> None:
        """Cancel execution and terminate the running FFmpeg process."""
        self._is_cancelled = True
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass

    def run(self) -> None:
        ffmpeg_bin = _ffmpeg_path()
        last_output = ""
        total = len(self._tasks)

        for idx, (label, args, out_target) in enumerate(self._tasks, start=1):
            if self._is_cancelled:
                self._cleanup_temps()
                self.finishedTask.emit(False, t("Cancelled by user"), "")
                return

            self.progressText.emit(f"[{idx}/{total}] {label}...")
            cmd = [ffmpeg_bin, "-hide_banner", "-y", *args]
            try:
                self._process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **subprocess_hidden_kwargs(),
                )
                _, stderr = self._process.communicate()
                ret = self._process.returncode
            except Exception as exc:
                self._cleanup_temps()
                self.finishedTask.emit(False, f"Process error: {exc}", "")
                return

            if self._is_cancelled:
                self._cleanup_temps()
                self.finishedTask.emit(False, t("Cancelled by user"), "")
                return

            if ret != 0:
                err_text = stderr.decode("utf-8", errors="replace")[-500:] if stderr else "Unknown error"
                self._cleanup_temps()
                self.finishedTask.emit(False, f"FFmpeg failed (code {ret}): {err_text}", "")
                return

            last_output = out_target

        self._cleanup_temps()
        self.finishedTask.emit(True, t("Completed successfully!"), last_output)

    def _cleanup_temps(self) -> None:
        for p in self._temp_files:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass


# =========================================================================
# Main Media Tools Dialog Window
# =========================================================================

DIALOG_STYLE = """
QDialog {
    background-color: #0f1724;
    color: #e0eaf6;
}
QTabWidget::pane {
    border: 1px solid #23344d;
    background: #142032;
    border-radius: 8px;
}
QTabBar::tab {
    background: #101c2e;
    color: #8ea3bf;
    padding: 9px 20px;
    margin-right: 4px;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    font-weight: 600;
    font-size: 13px;
}
QTabBar::tab:selected {
    background: #142032;
    color: #8ad7ff;
    border-bottom: 2px solid #8ad7ff;
}
QLabel {
    color: #d7e3f4;
    font-size: 12px;
}
QLineEdit {
    background-color: #1a2638;
    color: #ffffff;
    border: 1px solid #2c3f58;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 13px;
    min-height: 24px;
}
QLineEdit:focus {
    border: 1px solid #4ecdc4;
}
QSpinBox {
    background-color: #1a2638;
    color: #ffffff;
    border: 1px solid #2c3f58;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 13px;
    font-weight: 600;
    min-width: 90px;
    min-height: 28px;
}
QAbstractSpinBox QLineEdit, QSpinBox QLineEdit {
    background: transparent;
    color: #ffffff;
    border: none;
    padding: 0px 2px;
    margin: 0px;
    min-height: 0px;
}
QComboBox {
    background-color: #1a2638;
    color: #ffffff;
    border: 1px solid #2c3f58;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 13px;
    min-height: 24px;
}
QComboBox QAbstractItemView {
    background-color: #142032;
    color: #e0eaf6;
    selection-background-color: #22344d;
}
QPushButton {
    background-color: #22344d;
    color: #8ad7ff;
    font-weight: 600;
    font-size: 12px;
    border-radius: 6px;
    border: 1px solid #34506f;
    padding: 6px 14px;
}
QPushButton:hover {
    background-color: #2a4160;
}
QPushButton:disabled {
    background-color: #15202e;
    color: #4f637b;
    border-color: #1c2b3d;
}
QPushButton.primary-btn {
    background-color: #1a5b61;
    color: #4ecdc4;
    font-weight: 700;
    font-size: 13px;
    border: 1px solid #277981;
    padding: 8px 18px;
}
QPushButton.primary-btn:hover {
    background-color: #20727a;
    color: #ffffff;
}
QPushButton.preset-btn {
    background-color: #18273b;
    color: #8ea3bf;
    padding: 3px 8px;
    font-size: 11px;
}
QPushButton.preset-btn:hover {
    background-color: #243a57;
    color: #8ad7ff;
}
QListWidget {
    background-color: #162234;
    color: #e0eaf6;
    border: 1px solid #283a52;
    border-radius: 6px;
    padding: 4px;
    font-size: 12px;
}
QListWidget::item:selected {
    background-color: #253b59;
    color: #8ad7ff;
}
QProgressBar {
    background-color: #162234;
    border: 1px solid #283a52;
    border-radius: 4px;
    text-align: center;
    color: #e0eaf6;
    font-size: 11px;
    height: 14px;
}
QProgressBar::chunk {
    background-color: #4ecdc4;
    border-radius: 3px;
}
QCheckBox, QRadioButton {
    color: #d7e3f4;
    font-size: 12px;
    spacing: 8px;
}
"""


class MediaToolsDialog(QDialog):
    """Media Tools Dialog for CapCap video workflow: Split, Merge, Separate & Mux."""

    def __init__(self, parent: QWidget | None = None, initial_tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle(t("Media Tools — Split, Merge & Audio/Video"))
        self.setMinimumSize(820, 680)
        self.resize(840, 700)
        self.setStyleSheet(DIALOG_STYLE)

        self._worker: _MediaToolWorker | None = None
        self._last_output_location: str = ""

        # Layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 16, 18, 16)
        main_layout.setSpacing(12)

        # Header Title
        header_layout = QHBoxLayout()
        header_title = QLabel(t("Media Tools"), self)
        header_title.setStyleSheet("font-size: 18px; font-weight: 700; color: #8ad7ff;")
        header_layout.addWidget(header_title)
        header_sub = QLabel(t("Fast lossless video splitting, merging, audio separation & multiplexing"), self)
        header_sub.setStyleSheet("font-size: 12px; color: #8ea3bf; margin-left: 8px;")
        header_layout.addWidget(header_sub)
        header_layout.addStretch()
        main_layout.addLayout(header_layout)

        # Tab Widget
        self.tabs = QTabWidget(self)
        self._init_split_tab()
        self._init_merge_tab()
        self._init_extract_tab()
        self._init_mux_tab()
        self.tabs.setCurrentIndex(max(0, min(initial_tab, 3)))
        main_layout.addWidget(self.tabs, 1)

        # Shared Footer Bar (Progress, Status, Actions)
        footer_frame = QFrame(self)
        footer_frame.setObjectName("footerFrame")
        footer_frame.setStyleSheet("QFrame#footerFrame { background: #142032; border: 1px solid #23344d; border-radius: 6px; }")
        footer_layout = QVBoxLayout(footer_frame)
        footer_layout.setContentsMargins(10, 8, 10, 8)
        footer_layout.setSpacing(6)

        # Status & Progress Row
        status_row = QHBoxLayout()
        self.status_label = QLabel(t("Ready"), footer_frame)
        self.status_label.setStyleSheet("color: #8ea3bf; font-weight: 600; border: none; background: transparent;")
        status_row.addWidget(self.status_label, 1)

        self.progress_bar = QProgressBar(footer_frame)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setTextVisible(False)
        status_row.addWidget(self.progress_bar, 1)
        footer_layout.addLayout(status_row)

        # Button Actions Row
        actions_row = QHBoxLayout()
        self.cancel_btn = QPushButton(t("Cancel"), footer_frame)
        self.cancel_btn.setEnabled(False)
        self.cancel_btn.clicked.connect(self._on_cancel)
        actions_row.addWidget(self.cancel_btn)

        self.open_folder_btn = QPushButton(t("Open Output Folder"), footer_frame)
        self.open_folder_btn.setEnabled(False)
        self.open_folder_btn.clicked.connect(self._on_open_output_folder)
        actions_row.addWidget(self.open_folder_btn)

        actions_row.addStretch()

        self.close_btn = QPushButton(t("Close"), footer_frame)
        self.close_btn.clicked.connect(self.close)
        actions_row.addWidget(self.close_btn)
        footer_layout.addLayout(actions_row)

        main_layout.addWidget(footer_frame)

        # Localize tree
        localize_widget_tree(self)

    # =========================================================================
    # Tab 1: Split / Trim Video
    # =========================================================================

    def _init_split_tab(self) -> None:
        split_tab = QWidget()
        layout = QVBoxLayout(split_tab)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(10)

        # Input Video File
        in_grid = QGridLayout()
        in_grid.setSpacing(8)

        lbl_video = QLabel(t("Video File:"), split_tab)
        lbl_video.setFixedHeight(34)
        lbl_video.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        in_grid.addWidget(lbl_video, 0, 0)
        self.split_in_edit = QLineEdit(split_tab)
        self.split_in_edit.setFixedHeight(34)
        self.split_in_edit.setPlaceholderText(t("Select a video file to split or trim..."))
        self.split_in_edit.textChanged.connect(self._on_split_input_changed)
        in_grid.addWidget(self.split_in_edit, 0, 1)

        split_browse_btn = QPushButton(t("Browse..."), split_tab)
        split_browse_btn.setFixedHeight(34)
        split_browse_btn.clicked.connect(self._on_browse_split_input)
        in_grid.addWidget(split_browse_btn, 0, 2)

        self.split_dur_label = QLabel(t("Duration: --:--:--"), split_tab)
        self.split_dur_label.setStyleSheet("color: #4ecdc4; font-weight: 600;")
        in_grid.addWidget(self.split_dur_label, 1, 1, 1, 2)

        # Output Folder
        lbl_out = QLabel(t("Output Folder:"), split_tab)
        lbl_out.setFixedHeight(34)
        lbl_out.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        in_grid.addWidget(lbl_out, 2, 0)
        self.split_out_edit = QLineEdit(split_tab)
        self.split_out_edit.setFixedHeight(34)
        self.split_out_edit.setPlaceholderText(t("Output folder for split clips..."))
        in_grid.addWidget(self.split_out_edit, 2, 1)

        split_out_browse_btn = QPushButton(t("Browse..."), split_tab)
        split_out_browse_btn.setFixedHeight(34)
        split_out_browse_btn.clicked.connect(self._on_browse_split_output)
        in_grid.addWidget(split_out_browse_btn, 2, 2)

        layout.addLayout(in_grid)

        # Modes GroupBox
        modes_group = QGroupBox(t("Split / Trim Options"), split_tab)
        modes_group.setStyleSheet("QGroupBox { font-weight: 600; color: #8ad7ff; border: 1px solid #23344d; border-radius: 6px; margin-top: 10px; padding-top: 12px; }")
        modes_layout = QVBoxLayout(modes_group)
        modes_layout.setContentsMargins(14, 10, 14, 12)
        modes_layout.setSpacing(8)

        LABEL1_W = 125
        INPUT_W = 130
        LABEL2_W = 115
        ROW_H = 34

        self.split_btn_group = QButtonGroup(split_tab)

        # Option A: Duration
        self.rb_duration = QRadioButton(t("By segment duration"), modes_group)
        self.rb_duration.setChecked(True)
        self.split_btn_group.addButton(self.rb_duration)
        modes_layout.addWidget(self.rb_duration)

        dur_row = QHBoxLayout()
        dur_row.setContentsMargins(24, 0, 0, 0)
        dur_row.setSpacing(8)
        lbl_mins = QLabel(t("Minutes:"), modes_group)
        lbl_mins.setFixedSize(LABEL1_W, ROW_H)
        lbl_mins.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        dur_row.addWidget(lbl_mins)

        self.split_mins_spin = NoWheelSpinBox(modes_group)
        self.split_mins_spin.setRange(0, 1440)
        self.split_mins_spin.setValue(30)
        self.split_mins_spin.setFixedSize(INPUT_W, ROW_H)
        self.split_mins_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.split_mins_spin.valueChanged.connect(self._update_split_preview)
        dur_row.addWidget(self.split_mins_spin)

        dur_row.addSpacing(16)

        lbl_secs = QLabel(t("Seconds:"), modes_group)
        lbl_secs.setFixedSize(LABEL2_W, ROW_H)
        lbl_secs.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        dur_row.addWidget(lbl_secs)

        self.split_secs_spin = NoWheelSpinBox(modes_group)
        self.split_secs_spin.setRange(0, 59)
        self.split_secs_spin.setValue(0)
        self.split_secs_spin.setFixedSize(INPUT_W, ROW_H)
        self.split_secs_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.split_secs_spin.valueChanged.connect(self._update_split_preview)
        dur_row.addWidget(self.split_secs_spin)
        dur_row.addStretch()
        modes_layout.addLayout(dur_row)

        # Quick presets
        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(24, 0, 0, 2)
        preset_row.setSpacing(8)
        lbl_presets = QLabel(t("Presets:"), modes_group)
        lbl_presets.setFixedSize(LABEL1_W, 30)
        lbl_presets.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        preset_row.addWidget(lbl_presets)
        for label, m in [("1m", 1), ("5m", 5), ("15m", 15), ("30m", 30), ("60m", 60), ("120m", 120)]:
            btn = QPushButton(label, modes_group)
            btn.setProperty("class", "preset-btn")
            btn.setFixedSize(56, 30)
            btn.clicked.connect(lambda _, mins=m: self._set_split_preset(mins))
            preset_row.addWidget(btn)
        preset_row.addStretch()
        modes_layout.addLayout(preset_row)

        # Option B: Equal Parts
        self.rb_parts = QRadioButton(t("By equal parts"), modes_group)
        self.split_btn_group.addButton(self.rb_parts)
        modes_layout.addWidget(self.rb_parts)

        parts_row = QHBoxLayout()
        parts_row.setContentsMargins(24, 0, 0, 2)
        parts_row.setSpacing(8)
        lbl_parts = QLabel(t("Number of parts:"), modes_group)
        lbl_parts.setFixedSize(LABEL1_W, ROW_H)
        lbl_parts.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        parts_row.addWidget(lbl_parts)

        self.split_parts_spin = NoWheelSpinBox(modes_group)
        self.split_parts_spin.setRange(2, 200)
        self.split_parts_spin.setValue(2)
        self.split_parts_spin.setFixedSize(INPUT_W, ROW_H)
        self.split_parts_spin.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.split_parts_spin.valueChanged.connect(self._update_split_preview)
        parts_row.addWidget(self.split_parts_spin)
        parts_row.addStretch()
        modes_layout.addLayout(parts_row)

        # Option C: Trim Range
        self.rb_trim = QRadioButton(t("Trim range (Start → End)"), modes_group)
        self.split_btn_group.addButton(self.rb_trim)
        modes_layout.addWidget(self.rb_trim)

        trim_row = QHBoxLayout()
        trim_row.setContentsMargins(24, 0, 0, 0)
        trim_row.setSpacing(8)
        lbl_start = QLabel(t("Start Time:"), modes_group)
        lbl_start.setFixedSize(LABEL1_W, ROW_H)
        lbl_start.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        trim_row.addWidget(lbl_start)

        self.trim_start_edit = QLineEdit("00:00:00", modes_group)
        self.trim_start_edit.setFixedSize(INPUT_W, ROW_H)
        self.trim_start_edit.setAlignment(Qt.AlignCenter)
        self.trim_start_edit.textChanged.connect(self._update_split_preview)
        trim_row.addWidget(self.trim_start_edit)

        trim_row.addSpacing(16)

        lbl_end = QLabel(t("End Time:"), modes_group)
        lbl_end.setFixedSize(LABEL2_W, ROW_H)
        lbl_end.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        trim_row.addWidget(lbl_end)

        self.trim_end_edit = QLineEdit("00:00:00", modes_group)
        self.trim_end_edit.setFixedSize(INPUT_W, ROW_H)
        self.trim_end_edit.setAlignment(Qt.AlignCenter)
        self.trim_end_edit.textChanged.connect(self._update_split_preview)
        trim_row.addWidget(self.trim_end_edit)
        trim_row.addStretch()
        modes_layout.addLayout(trim_row)

        self.rb_duration.toggled.connect(self._update_split_preview)
        self.rb_parts.toggled.connect(self._update_split_preview)
        self.rb_trim.toggled.connect(self._update_split_preview)

        layout.addWidget(modes_group)

        # Estimated Preview Label
        self.split_preview_label = QLabel(t("Estimated output: --"), split_tab)
        self.split_preview_label.setStyleSheet("color: #8ea3bf; font-style: italic;")
        layout.addWidget(self.split_preview_label)

        layout.addStretch()

        # Primary Action
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.split_start_btn = QPushButton(t("Start Split"), split_tab)
        self.split_start_btn.setProperty("class", "primary-btn")
        self.split_start_btn.setStyleSheet(DIALOG_STYLE)
        self.split_start_btn.clicked.connect(self._on_start_split)
        action_row.addWidget(self.split_start_btn)
        layout.addLayout(action_row)

        self.tabs.addTab(split_tab, t("Split / Trim Video"))

    def _set_split_preset(self, mins: int) -> None:
        self.split_mins_spin.setValue(mins)
        self.split_secs_spin.setValue(0)
        self.rb_duration.setChecked(True)

    def _on_browse_split_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("Select Video to Split"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;All Files (*)")
        )
        if path:
            self.split_in_edit.setText(os.path.normpath(path))

    def _on_browse_split_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Select Output Folder"))
        if folder:
            self.split_out_edit.setText(os.path.normpath(folder))

    def _on_split_input_changed(self, path: str) -> None:
        clean_p = path.strip()
        if clean_p and os.path.exists(clean_p):
            dur = _get_video_duration(clean_p)
            self._split_video_dur = dur
            self.split_dur_label.setText(t("Duration: {time}", time=format_hms(dur)))
            self.trim_end_edit.setText(format_hms(dur))
            base_dir = os.path.dirname(clean_p)
            stem = os.path.splitext(os.path.basename(clean_p))[0]
            if not self.split_out_edit.text().strip():
                self.split_out_edit.setText(os.path.join(base_dir, f"{stem}_parts"))
        else:
            self._split_video_dur = 0.0
            self.split_dur_label.setText(t("Duration: --:--:--"))
        self._update_split_preview()

    def _update_split_preview(self) -> None:
        dur = getattr(self, "_split_video_dur", 0.0)
        if dur <= 0:
            self.split_preview_label.setText(t("Estimated output: Select a video to preview."))
            return

        if self.rb_duration.isChecked():
            seg_s = self.split_mins_spin.value() * 60 + self.split_secs_spin.value()
            if seg_s <= 0:
                self.split_preview_label.setText(t("Estimated output: Please specify a duration > 0s."))
                return
            count = max(1, math.ceil(dur / seg_s))
            self.split_preview_label.setText(
                t("Estimated output: ~{count} segment(s) of ~{seg_time}", count=count, seg_time=format_hms(seg_s))
            )
        elif self.rb_parts.isChecked():
            parts = max(1, self.split_parts_spin.value())
            seg_s = dur / parts
            self.split_preview_label.setText(
                t("Estimated output: {count} parts of ~{seg_time} each", count=parts, seg_time=format_hms(seg_s))
            )
        else:
            start_s = parse_hms(self.trim_start_edit.text())
            end_s = parse_hms(self.trim_end_edit.text())
            if start_s < 0 or end_s < 0 or end_s <= start_s:
                self.split_preview_label.setText(t("Estimated output: Invalid Start or End time."))
            else:
                trim_len = min(dur, end_s) - start_s
                self.split_preview_label.setText(
                    t("Estimated output: 1 trimmed clip ({len_time})", len_time=format_hms(trim_len))
                )

    def _on_start_split(self) -> None:
        in_path = self.split_in_edit.text().strip()
        if not in_path or not os.path.exists(in_path):
            QMessageBox.warning(self, t("Invalid Input"), t("Please select an existing video file."))
            return
        out_dir = self.split_out_edit.text().strip()
        if not out_dir:
            out_dir = os.path.dirname(in_path)
        os.makedirs(out_dir, exist_ok=True)

        dur = getattr(self, "_split_video_dur", 0.0)
        if self.rb_duration.isChecked():
            mode = "duration"
            seg_s = float(self.split_mins_spin.value() * 60 + self.split_secs_spin.value())
            if seg_s <= 0:
                QMessageBox.warning(self, t("Invalid Duration"), t("Segment duration must be greater than 0."))
                return
            args, target = build_split_ffmpeg_args(in_path, out_dir, mode, segment_seconds=seg_s, total_duration=dur)
            label = t("Splitting by duration ({time})", time=format_hms(seg_s))
        elif self.rb_parts.isChecked():
            mode = "parts"
            parts = self.split_parts_spin.value()
            args, target = build_split_ffmpeg_args(in_path, out_dir, mode, num_parts=parts, total_duration=dur)
            label = t("Splitting into {parts} parts", parts=parts)
        else:
            mode = "trim"
            start_s = parse_hms(self.trim_start_edit.text())
            end_s = parse_hms(self.trim_end_edit.text())
            if start_s < 0 or end_s < 0 or end_s <= start_s:
                QMessageBox.warning(self, t("Invalid Range"), t("End time must be greater than start time."))
                return
            args, target = build_split_ffmpeg_args(in_path, out_dir, mode, trim_start_s=start_s, trim_end_s=end_s)
            label = t("Trimming range {start} → {end}", start=format_hms(start_s), end=format_hms(end_s))

        self._start_task([(label, args, target)])

    # =========================================================================
    # Tab 2: Merge Videos
    # =========================================================================

    def _init_merge_tab(self) -> None:
        merge_tab = QWidget()
        layout = QVBoxLayout(merge_tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        # List & Action Buttons
        list_label = QLabel(t("Videos to merge (order matters):"), merge_tab)
        layout.addWidget(list_label)

        list_row = QHBoxLayout()
        self.merge_list = QListWidget(merge_tab)
        list_row.addWidget(self.merge_list, 1)

        btns_layout = QVBoxLayout()
        btns_layout.setSpacing(6)

        add_btn = QPushButton(t("+ Add Videos"), merge_tab)
        add_btn.clicked.connect(self._on_merge_add_videos)
        btns_layout.addWidget(add_btn)

        up_btn = QPushButton(t("Move Up"), merge_tab)
        up_btn.clicked.connect(self._on_merge_move_up)
        btns_layout.addWidget(up_btn)

        down_btn = QPushButton(t("Move Down"), merge_tab)
        down_btn.clicked.connect(self._on_merge_move_down)
        btns_layout.addWidget(down_btn)

        remove_btn = QPushButton(t("Remove"), merge_tab)
        remove_btn.clicked.connect(self._on_merge_remove)
        btns_layout.addWidget(remove_btn)

        clear_btn = QPushButton(t("Clear"), merge_tab)
        clear_btn.clicked.connect(self.merge_list.clear)
        btns_layout.addWidget(clear_btn)
        btns_layout.addStretch()

        list_row.addLayout(btns_layout)
        layout.addLayout(list_row)

        # Merge Options
        opt_grid = QGridLayout()
        opt_grid.setSpacing(8)

        lbl_mode = QLabel(t("Merge Mode:"), merge_tab)
        lbl_mode.setFixedHeight(34)
        lbl_mode.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        opt_grid.addWidget(lbl_mode, 0, 0)
        self.merge_mode_combo = QComboBox(merge_tab)
        self.merge_mode_combo.setFixedHeight(34)
        self.merge_mode_combo.addItem(t("Fast Copy (No re-encode, same format)"), "copy")
        self.merge_mode_combo.addItem(t("Standardize & Re-encode (1080p H.264/AAC)"), "reencode")
        opt_grid.addWidget(self.merge_mode_combo, 0, 1, 1, 2)

        lbl_out = QLabel(t("Output File:"), merge_tab)
        lbl_out.setFixedHeight(34)
        lbl_out.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        opt_grid.addWidget(lbl_out, 1, 0)
        self.merge_out_edit = QLineEdit(merge_tab)
        self.merge_out_edit.setFixedHeight(34)
        self.merge_out_edit.setPlaceholderText(t("Target merged video file (.mp4)..."))
        opt_grid.addWidget(self.merge_out_edit, 1, 1)

        merge_out_browse_btn = QPushButton(t("Browse..."), merge_tab)
        merge_out_browse_btn.setFixedHeight(34)
        merge_out_browse_btn.clicked.connect(self._on_browse_merge_output)
        opt_grid.addWidget(merge_out_browse_btn, 1, 2)

        layout.addLayout(opt_grid)
        layout.addStretch()

        # Primary Action
        action_row = QHBoxLayout()
        action_row.addStretch()
        self.merge_start_btn = QPushButton(t("Start Merge"), merge_tab)
        self.merge_start_btn.setProperty("class", "primary-btn")
        self.merge_start_btn.setStyleSheet(DIALOG_STYLE)
        self.merge_start_btn.clicked.connect(self._on_start_merge)
        action_row.addWidget(self.merge_start_btn)
        layout.addLayout(action_row)

        self.tabs.addTab(merge_tab, t("Merge Videos"))

    def _on_merge_add_videos(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self, t("Select Videos to Merge"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;All Files (*)")
        )
        if files:
            for f in files:
                norm = os.path.normpath(f)
                self.merge_list.addItem(norm)
            if not self.merge_out_edit.text().strip():
                first = self.merge_list.item(0).text()
                dir_name = os.path.dirname(first)
                stem = os.path.splitext(os.path.basename(first))[0]
                self.merge_out_edit.setText(os.path.join(dir_name, f"{stem}_merged.mp4"))

    def _on_merge_move_up(self) -> None:
        row = self.merge_list.currentRow()
        if row > 0:
            item = self.merge_list.takeItem(row)
            self.merge_list.insertItem(row - 1, item)
            self.merge_list.setCurrentRow(row - 1)

    def _on_merge_move_down(self) -> None:
        row = self.merge_list.currentRow()
        if 0 <= row < self.merge_list.count() - 1:
            item = self.merge_list.takeItem(row)
            self.merge_list.insertItem(row + 1, item)
            self.merge_list.setCurrentRow(row + 1)

    def _on_merge_remove(self) -> None:
        row = self.merge_list.currentRow()
        if row >= 0:
            self.merge_list.takeItem(row)

    def _on_browse_merge_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, t("Save Merged Video As"), "",
            t("MP4 Video (*.mp4);;MKV Video (*.mkv);;All Files (*)")
        )
        if path:
            self.merge_out_edit.setText(os.path.normpath(path))

    def _on_start_merge(self) -> None:
        count = self.merge_list.count()
        if count < 2:
            QMessageBox.warning(self, t("Not Enough Videos"), t("Please add at least 2 videos to merge."))
            return
        video_paths = [self.merge_list.item(i).text() for i in range(count)]
        for p in video_paths:
            if not os.path.exists(p):
                QMessageBox.warning(self, t("File Not Found"), t("Video file does not exist: {file}", file=p))
                return

        out_file = self.merge_out_edit.text().strip()
        if not out_file:
            QMessageBox.warning(self, t("Output Required"), t("Please specify an output file path."))
            return
        os.makedirs(os.path.dirname(os.path.abspath(out_file)), exist_ok=True)

        reencode = (self.merge_mode_combo.currentData() == "reencode")

        # Concat list file in temp
        list_fd, list_path = tempfile.mkstemp(prefix="capcap_concat_", suffix=".txt")
        os.close(list_fd)
        write_concat_list_file(video_paths, list_path)

        args = build_merge_ffmpeg_args(video_paths, out_file, list_path, reencode=reencode)
        label = t("Merging {count} videos ({mode})", count=count, mode="Re-encode" if reencode else "Copy")
        self._start_task([(label, args, out_file)], temp_files=[list_path])

    # =========================================================================
    # Tab 3: Separate Audio / Video
    # =========================================================================

    def _init_extract_tab(self) -> None:
        extract_tab = QWidget()
        layout = QVBoxLayout(extract_tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(8)

        lbl_video = QLabel(t("Video File:"), extract_tab)
        lbl_video.setFixedHeight(34)
        lbl_video.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_video, 0, 0)
        self.extract_in_edit = QLineEdit(extract_tab)
        self.extract_in_edit.setFixedHeight(34)
        self.extract_in_edit.setPlaceholderText(t("Select a video file to separate..."))
        self.extract_in_edit.textChanged.connect(self._on_extract_input_changed)
        grid.addWidget(self.extract_in_edit, 0, 1)

        extract_browse_btn = QPushButton(t("Browse..."), extract_tab)
        extract_browse_btn.setFixedHeight(34)
        extract_browse_btn.clicked.connect(self._on_browse_extract_input)
        grid.addWidget(extract_browse_btn, 0, 2)

        self.extract_dur_label = QLabel(t("Duration: --:--:--"), extract_tab)
        self.extract_dur_label.setStyleSheet("color: #4ecdc4; font-weight: 600;")
        grid.addWidget(self.extract_dur_label, 1, 1, 1, 2)

        lbl_opt = QLabel(t("Extract Option:"), extract_tab)
        lbl_opt.setFixedHeight(34)
        lbl_opt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_opt, 2, 0)
        self.extract_mode_combo = QComboBox(extract_tab)
        self.extract_mode_combo.setFixedHeight(34)
        self.extract_mode_combo.addItem(t("Extract both Audio and Muted Video"), "both")
        self.extract_mode_combo.addItem(t("Extract Audio only"), "audio_only")
        self.extract_mode_combo.addItem(t("Extract Muted Video only (remove audio)"), "video_only")
        self.extract_mode_combo.currentIndexChanged.connect(self._on_extract_mode_changed)
        grid.addWidget(self.extract_mode_combo, 2, 1, 1, 2)

        lbl_fmt = QLabel(t("Audio Format:"), extract_tab)
        lbl_fmt.setFixedHeight(34)
        lbl_fmt.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_fmt, 3, 0)
        self.extract_fmt_combo = QComboBox(extract_tab)
        self.extract_fmt_combo.setFixedHeight(34)
        self.extract_fmt_combo.addItem("MP3 (.mp3)", "mp3")
        self.extract_fmt_combo.addItem("WAV (.wav)", "wav")
        self.extract_fmt_combo.addItem("AAC / M4A (.m4a)", "m4a")
        grid.addWidget(self.extract_fmt_combo, 3, 1, 1, 2)

        lbl_out = QLabel(t("Output Folder:"), extract_tab)
        lbl_out.setFixedHeight(34)
        lbl_out.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_out, 4, 0)
        self.extract_out_edit = QLineEdit(extract_tab)
        self.extract_out_edit.setFixedHeight(34)
        self.extract_out_edit.setPlaceholderText(t("Output directory..."))
        grid.addWidget(self.extract_out_edit, 4, 1)

        extract_out_browse_btn = QPushButton(t("Browse..."), extract_tab)
        extract_out_browse_btn.setFixedHeight(34)
        extract_out_browse_btn.clicked.connect(self._on_browse_extract_output)
        grid.addWidget(extract_out_browse_btn, 4, 2)

        layout.addLayout(grid)
        layout.addStretch()

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.extract_start_btn = QPushButton(t("Start Extract"), extract_tab)
        self.extract_start_btn.setProperty("class", "primary-btn")
        self.extract_start_btn.setStyleSheet(DIALOG_STYLE)
        self.extract_start_btn.clicked.connect(self._on_start_extract)
        action_row.addWidget(self.extract_start_btn)
        layout.addLayout(action_row)

        self.tabs.addTab(extract_tab, t("Separate Audio / Video"))

    def _on_browse_extract_input(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("Select Video to Separate"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;All Files (*)")
        )
        if path:
            self.extract_in_edit.setText(os.path.normpath(path))

    def _on_browse_extract_output(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, t("Select Output Folder"))
        if folder:
            self.extract_out_edit.setText(os.path.normpath(folder))

    def _on_extract_input_changed(self, path: str) -> None:
        clean = path.strip()
        if clean and os.path.exists(clean):
            dur = _get_video_duration(clean)
            self.extract_dur_label.setText(t("Duration: {time}", time=format_hms(dur)))
            if not self.extract_out_edit.text().strip():
                self.extract_out_edit.setText(os.path.dirname(clean))
        else:
            self.extract_dur_label.setText(t("Duration: --:--:--"))

    def _on_extract_mode_changed(self, index: int) -> None:
        mode = self.extract_mode_combo.currentData()
        self.extract_fmt_combo.setEnabled(mode in ("both", "audio_only"))

    def _on_start_extract(self) -> None:
        in_path = self.extract_in_edit.text().strip()
        if not in_path or not os.path.exists(in_path):
            QMessageBox.warning(self, t("Invalid Input"), t("Please select an existing video file."))
            return
        out_dir = self.extract_out_edit.text().strip() or os.path.dirname(in_path)
        os.makedirs(out_dir, exist_ok=True)

        mode = self.extract_mode_combo.currentData()
        fmt = self.extract_fmt_combo.currentData()
        tasks = build_extract_ffmpeg_tasks(in_path, out_dir, mode, fmt)
        if not tasks:
            return

        self._start_task(tasks)

    # =========================================================================
    # Tab 4: Mux Audio + Video
    # =========================================================================

    def _init_mux_tab(self) -> None:
        mux_tab = QWidget()
        layout = QVBoxLayout(mux_tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        grid = QGridLayout()
        grid.setSpacing(8)

        lbl_video = QLabel(t("Video File (Image/Stream):"), mux_tab)
        lbl_video.setFixedHeight(34)
        lbl_video.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_video, 0, 0)
        self.mux_video_edit = QLineEdit(mux_tab)
        self.mux_video_edit.setFixedHeight(34)
        self.mux_video_edit.setPlaceholderText(t("Select base video file..."))
        self.mux_video_edit.textChanged.connect(self._on_mux_input_changed)
        grid.addWidget(self.mux_video_edit, 0, 1)

        mux_v_browse_btn = QPushButton(t("Browse..."), mux_tab)
        mux_v_browse_btn.setFixedHeight(34)
        mux_v_browse_btn.clicked.connect(self._on_browse_mux_video)
        grid.addWidget(mux_v_browse_btn, 0, 2)

        lbl_audio = QLabel(t("Audio File (Soundtrack):"), mux_tab)
        lbl_audio.setFixedHeight(34)
        lbl_audio.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_audio, 1, 0)
        self.mux_audio_edit = QLineEdit(mux_tab)
        self.mux_audio_edit.setFixedHeight(34)
        self.mux_audio_edit.setPlaceholderText(t("Select replacement or soundtrack audio file..."))
        grid.addWidget(self.mux_audio_edit, 1, 1)

        mux_a_browse_btn = QPushButton(t("Browse..."), mux_tab)
        mux_a_browse_btn.setFixedHeight(34)
        mux_a_browse_btn.clicked.connect(self._on_browse_mux_audio)
        grid.addWidget(mux_a_browse_btn, 1, 2)

        self.mux_shortest_cb = QCheckBox(t("Trim output to the shorter stream (-shortest)"), mux_tab)
        self.mux_shortest_cb.setChecked(True)
        grid.addWidget(self.mux_shortest_cb, 2, 1, 1, 2)

        lbl_out = QLabel(t("Output File:"), mux_tab)
        lbl_out.setFixedHeight(34)
        lbl_out.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        grid.addWidget(lbl_out, 3, 0)
        self.mux_out_edit = QLineEdit(mux_tab)
        self.mux_out_edit.setFixedHeight(34)
        self.mux_out_edit.setPlaceholderText(t("Target muxed video file (.mp4)..."))
        grid.addWidget(self.mux_out_edit, 3, 1)

        mux_out_browse_btn = QPushButton(t("Browse..."), mux_tab)
        mux_out_browse_btn.setFixedHeight(34)
        mux_out_browse_btn.clicked.connect(self._on_browse_mux_output)
        grid.addWidget(mux_out_browse_btn, 3, 2)

        layout.addLayout(grid)
        layout.addStretch()

        action_row = QHBoxLayout()
        action_row.addStretch()
        self.mux_start_btn = QPushButton(t("Start Mux"), mux_tab)
        self.mux_start_btn.setProperty("class", "primary-btn")
        self.mux_start_btn.setStyleSheet(DIALOG_STYLE)
        self.mux_start_btn.clicked.connect(self._on_start_mux)
        action_row.addWidget(self.mux_start_btn)
        layout.addLayout(action_row)

        self.tabs.addTab(mux_tab, t("Mux Audio + Video"))

    def _on_browse_mux_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("Select Base Video"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm *.ts);;All Files (*)")
        )
        if path:
            self.mux_video_edit.setText(os.path.normpath(path))

    def _on_browse_mux_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, t("Select Audio Track"), "",
            t("Audio Files (*.mp3 *.wav *.m4a *.aac *.flac *.ogg);;All Files (*)")
        )
        if path:
            self.mux_audio_edit.setText(os.path.normpath(path))

    def _on_browse_mux_output(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, t("Save Muxed Video As"), "",
            t("MP4 Video (*.mp4);;MKV Video (*.mkv);;All Files (*)")
        )
        if path:
            self.mux_out_edit.setText(os.path.normpath(path))

    def _on_mux_input_changed(self, path: str) -> None:
        clean = path.strip()
        if clean and os.path.exists(clean) and not self.mux_out_edit.text().strip():
            base_dir = os.path.dirname(clean)
            stem = os.path.splitext(os.path.basename(clean))[0]
            self.mux_out_edit.setText(os.path.join(base_dir, f"{stem}_muxed.mp4"))

    def _on_start_mux(self) -> None:
        v_path = self.mux_video_edit.text().strip()
        if not v_path or not os.path.exists(v_path):
            QMessageBox.warning(self, t("Invalid Video"), t("Please select an existing video file."))
            return
        a_path = self.mux_audio_edit.text().strip()
        if not a_path or not os.path.exists(a_path):
            QMessageBox.warning(self, t("Invalid Audio"), t("Please select an existing audio file."))
            return
        out_path = self.mux_out_edit.text().strip()
        if not out_path:
            QMessageBox.warning(self, t("Output Required"), t("Please specify an output video file path."))
            return
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        args = build_mux_ffmpeg_args(v_path, a_path, out_path, shortest=self.mux_shortest_cb.isChecked())
        label = t("Muxing video with audio track")
        self._start_task([(label, args, out_path)])

    # =========================================================================
    # Task Execution & Lifecycle
    # =========================================================================

    def _set_ui_busy(self, busy: bool) -> None:
        self.tabs.setEnabled(not busy)
        self.split_start_btn.setEnabled(not busy)
        self.merge_start_btn.setEnabled(not busy)
        self.extract_start_btn.setEnabled(not busy)
        self.mux_start_btn.setEnabled(not busy)
        self.cancel_btn.setEnabled(busy)
        self.close_btn.setEnabled(not busy)

        if busy:
            self.progress_bar.setRange(0, 0)  # Indeterminate
        else:
            self.progress_bar.setRange(0, 100)

    def _start_task(
        self,
        tasks: list[tuple[str, list[str], str]],
        temp_files: list[str] | None = None,
    ) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self._set_ui_busy(True)
        self.open_folder_btn.setEnabled(False)
        self.status_label.setText(t("Starting task..."))
        self.status_label.setStyleSheet("color: #8ad7ff; font-weight: 600;")

        self._worker = _MediaToolWorker(tasks, temp_files=temp_files, parent=self)
        self._worker.progressText.connect(self._on_worker_progress)
        self._worker.finishedTask.connect(self._on_worker_finished)
        self._worker.start()

    def _on_worker_progress(self, text: str) -> None:
        self.status_label.setText(text)

    def _on_worker_finished(self, success: bool, message: str, output_target: str) -> None:
        self._set_ui_busy(False)
        if success:
            self.progress_bar.setValue(100)
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: #4ecdc4; font-weight: 600;")
            self._last_output_location = output_target
            if output_target:
                self.open_folder_btn.setEnabled(True)
            QMessageBox.information(self, t("Success"), t("Task finished successfully!\n\nOutput: {out}", out=output_target))
        else:
            self.progress_bar.setValue(0)
            self.status_label.setText(message)
            self.status_label.setStyleSheet("color: #ff6b6b; font-weight: 600;")
            QMessageBox.critical(self, t("Failed"), message)

    def _on_cancel(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self.status_label.setText(t("Cancelling..."))
            self._worker.cancel()

    def _on_open_output_folder(self) -> None:
        target = self._last_output_location
        if not target:
            return
        folder = target if os.path.isdir(target) else os.path.dirname(target)
        if os.path.exists(folder):
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def closeEvent(self, event) -> None:
        if self._worker is not None and self._worker.isRunning():
            reply = QMessageBox.question(
                self, t("Confirm Exit"),
                t("A media processing task is currently running. Cancel and exit?"),
                QMessageBox.Yes | QMessageBox.No, self,
            )
            if reply == QMessageBox.Yes:
                self._worker.cancel()
                self._worker.wait(1000)
                event.accept()
            else:
                event.ignore()
                return
        super().closeEvent(event)
