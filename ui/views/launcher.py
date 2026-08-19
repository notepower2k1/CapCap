import hashlib
import math
import os
import json
import time
import shutil
import hashlib
import re

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from runtime_paths import asset_path, subprocess_hidden_kwargs, workspace_root


def _thumbnail_name(video_path: str) -> str:
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:12]
    return f"thumb_{digest}.jpg"




def _recent_projects_path():
    # ``__file__`` lives inside ``_internal`` in a frozen build. Recent
    # project data belongs beside the executable, not inside bundled assets.
    return os.path.join(workspace_root(), "recent_projects.json")


def _load_recent_projects(settings=None):
    path = _recent_projects_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_recent_projects(settings, projects):
    path = _recent_projects_path()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


def _project_pipeline_status(video_path: str) -> tuple[str, str]:
    """Read the persisted project stage without creating or modifying it."""
    name = os.path.splitext(os.path.basename(video_path))[0] or "project"
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower() or "project"
    digest = hashlib.sha1(os.path.abspath(video_path).encode("utf-8")).hexdigest()[:8]
    state_path = os.path.join(workspace_root(), "projects", f"{slug}_{digest}", "project.json")
    try:
        with open(os.path.normpath(state_path), "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError, TypeError):
        return "Ready", "#8394aa"
    artifacts = dict(state.get("artifacts") or {})
    steps = dict(state.get("steps") or {})
    if artifacts.get("final_video"):
        return "Export complete", "#6ee7d6"
    if artifacts.get("voice_vi") or artifacts.get("mixed_vi"):
        return "TTS complete", "#6ee7d6"
    if str(steps.get("translate_raw", "")).lower() == "done" or artifacts.get("translation_final"):
        return "Translate complete", "#78b8ff"
    if artifacts.get("transcript_segments"):
        return "Transcript complete", "#f6c453"
    return "Ready", "#8394aa"


def _extract_thumbnail(video_path: str, output_path: str) -> str:
    if not os.path.exists(video_path):
        return ""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    import subprocess
    try:
        subprocess.run(
            [_ffmpeg_path(), "-y", "-i", video_path, "-vframes", "1", "-q:v", "3",
             "-vf", "scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
             output_path],
            capture_output=True, timeout=30, **subprocess_hidden_kwargs(),
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception:
        pass
    return ""


def _ffmpeg_path():
    from runtime_paths import bin_path
    return os.path.join(bin_path(), "ffmpeg", "ffmpeg.exe")


def _get_video_duration(video_path: str) -> float:
    try:
        import subprocess
        ffprobe = _ffmpeg_path().replace("ffmpeg.exe", "ffprobe.exe")
        result = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", video_path],
            capture_output=True, text=True, timeout=30, **subprocess_hidden_kwargs(),
        )
        if result.returncode == 0:
            return float(result.stdout.strip() or 0)
    except Exception:
        pass
    return 0.0


MSG_STYLE = """
    QMessageBox { background-color: #0f1724; }
    QLabel { color: #ffffff; }
    QPushButton { background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
        border-radius: 8px; padding: 6px 16px; font-weight: 600; }
    QPushButton:hover { background-color: #29405d; }
"""


class ProjectCard(QFrame):
    def __init__(self, video_path: str, thumbnail_cache_dir: str, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self._orig_pixmap = None
        self.setFixedSize(260, 224)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("projectCard")
        self.setStyleSheet("""
            #projectCard {
                background-color: #0d1827;
                border: 1px solid #1c304a;
                border-radius: 10px;
            }
            #projectCard:hover {
                background-color: #122238;
                border: 1px solid #38bdf8;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 16:9 Thumbnail container
        self.thumb_container = QWidget()
        self.thumb_container.setFixedSize(240, 135)
        self.thumb_container.setStyleSheet("background-color: #060b14; border-radius: 6px;")

        thumb_layout = QVBoxLayout(self.thumb_container)
        thumb_layout.setContentsMargins(0, 0, 0, 0)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(240, 135)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background-color: transparent; border-radius: 6px; color: #64748b; font-size: 11px;")
        thumb_layout.addWidget(self.thumb_label)

        # Duration badge overlaid at bottom right
        self.duration_badge = QLabel(self.thumb_container)
        self.duration_badge.setStyleSheet("""
            background-color: rgba(6, 11, 20, 0.85);
            color: #f1f5f9;
            font-size: 10px;
            font-weight: 700;
            border-radius: 4px;
            padding: 2px 6px;
            border: 1px solid rgba(255, 255, 255, 0.12);
        """)
        self.duration_badge.hide()

        layout.addWidget(self.thumb_container)

        # Video title (clean 2-line wrap)
        filename = os.path.basename(video_path)
        self.name_label = QLabel(filename)
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(36)
        self.name_label.setToolTip(filename)
        self.name_label.setStyleSheet("color: #f1f5f9; font-size: 12px; font-weight: 600;")
        layout.addWidget(self.name_label)

        # Bottom info row
        bottom_row = QHBoxLayout()
        bottom_row.setContentsMargins(0, 0, 0, 0)
        bottom_row.setSpacing(6)

        stage_text, stage_color = _project_pipeline_status(video_path)
        self.stage_badge = QLabel(stage_text)
        self.stage_badge.setAlignment(Qt.AlignCenter)
        self.stage_badge.setStyleSheet(
            f"background-color: #101c2d; color: {stage_color}; border: 1px solid #233b59; "
            "border-radius: 5px; padding: 2px 8px; font-size: 10px; font-weight: 700;"
        )
        bottom_row.addWidget(self.stage_badge)
        bottom_row.addStretch()

        file_size_text = ""
        try:
            if os.path.exists(video_path):
                size_mb = os.path.getsize(video_path) / (1024 * 1024)
                if size_mb >= 1024:
                    file_size_text = f"{size_mb / 1024:.1f} GB"
                else:
                    file_size_text = f"{size_mb:.1f} MB"
        except Exception:
            pass

        if file_size_text:
            self.size_label = QLabel(file_size_text)
            self.size_label.setStyleSheet("color: #64748b; font-size: 10px; font-weight: 500;")
            bottom_row.addWidget(self.size_label)

        layout.addLayout(bottom_row)

        self._load_thumb(thumbnail_cache_dir)
        self._load_duration()

    def _load_duration(self):
        try:
            duration_s = _get_video_duration(self.video_path)
            if duration_s > 0:
                mins = int(duration_s // 60)
                secs = int(duration_s % 60)
                hrs = int(mins // 60)
                mins = mins % 60
                if hrs > 0:
                    text = f"{hrs:02d}:{mins:02d}:{secs:02d}"
                else:
                    text = f"{mins:02d}:{secs:02d}"
                self.duration_badge.setText(text)
                self.duration_badge.adjustSize()
                self.duration_badge.move(
                    self.thumb_container.width() - self.duration_badge.width() - 6,
                    self.thumb_container.height() - self.duration_badge.height() - 6,
                )
                self.duration_badge.show()
        except Exception:
            pass

    def _load_thumb(self, cache_dir):
        thumb_path = os.path.join(cache_dir, _thumbnail_name(self.video_path))
        if not os.path.exists(thumb_path):
            thumb_path = _extract_thumbnail(self.video_path, thumb_path)
        if os.path.exists(thumb_path):
            self._orig_pixmap = QPixmap(thumb_path)
            if not self._orig_pixmap.isNull():
                scaled = self._orig_pixmap.scaled(
                    240, 135, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - 240) // 2)
                crop_y = max(0, (scaled.height() - 135) // 2)
                cropped = scaled.copy(crop_x, crop_y, 240, 135)
                self.thumb_label.setPixmap(cropped)
                return
        self.thumb_label.setText("No Preview")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self.isEnabled():
                event.ignore()
                return
            self.window().selected_video = self.video_path
            self.window().accept()
        super().mousePressEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #0f1928;
                color: #e2e8f0;
                border: 1px solid #233b59;
                border-radius: 8px;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 16px;
                border-radius: 4px;
            }
            QMenu::item:selected {
                background-color: #1e334e;
                color: #38bdf8;
            }
        """)
        open_action = menu.addAction("Open in Editor")
        open_folder_action = menu.addAction("Open Video Folder")
        menu.addSeparator()
        remove_action = menu.addAction("Remove from List")

        action = menu.exec(event.globalPos())
        if action == open_action:
            self.window().selected_video = self.video_path
            self.window().accept()
        elif action == open_folder_action:
            folder = os.path.dirname(os.path.abspath(self.video_path))
            if os.path.exists(folder) and hasattr(os, "startfile"):
                os.startfile(folder)
        elif action == remove_action:
            projects = _load_recent_projects()
            updated = [p for p in projects if p.get("video_path") != self.video_path]
            _save_recent_projects(None, updated)
            if hasattr(self.window(), "_load_recent"):
                self.window()._load_recent()



def _extract_waveform_audio(video_path: str, temp_root: str) -> str:
    video_hash = hashlib.md5(video_path.encode("utf-8")).hexdigest()[:12]
    audio_path = os.path.join(temp_root, f"waveform_{video_hash}.wav")
    if os.path.exists(audio_path):
        return audio_path
    if not os.path.exists(video_path):
        return ""
    os.makedirs(os.path.dirname(audio_path) or ".", exist_ok=True)
    import subprocess
    try:
        subprocess.run(
            [_ffmpeg_path(), "-y", "-loglevel", "error", "-i", video_path,
             "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
            check=True, timeout=60, **subprocess_hidden_kwargs(),
        )
        print(f"[Launcher] Waveform audio extracted: {audio_path}")
    except Exception as exc:
        print(f"[Launcher] Waveform extract failed: {exc}")
        return ""
    return audio_path if os.path.exists(audio_path) else ""


def _prepare_timeline_visual_cache(video_path: str, temp_root: str) -> None:
    """Build the editor's static V1/A1 cache before opening the editor."""
    try:
        import numpy as np
        import subprocess
        import wave

        source = os.path.abspath(video_path)
        stat = os.stat(source)
        digest = hashlib.md5(source.encode("utf-8")).hexdigest()[:12]
        cache_dir = os.path.join(temp_root, "timeline_visuals")
        thumb_dir = os.path.join(temp_root, "timeline_thumbnails")
        manifest_path = os.path.join(cache_dir, f"{digest}.json")
        os.makedirs(cache_dir, exist_ok=True)

        try:
            with open(manifest_path, "r", encoding="utf-8") as handle:
                existing = json.load(handle)
            if (
                int(existing.get("visual_version", 0)) == 4
                and
                existing.get("source") == source
                and existing.get("size") == int(stat.st_size)
                and existing.get("mtime_ns") == int(stat.st_mtime_ns)
                and existing.get("waveform")
                and all(os.path.exists(path) for _time, path in existing.get("thumbnails", []))
            ):
                print("[Launcher] Timeline visuals loaded from cache")
                return
        except (OSError, ValueError, TypeError):
            pass

        duration_s = _get_video_duration(source)
        if duration_s <= 60.0:
            interval_s = max(2.0, duration_s / 12.0)
        elif duration_s <= 300.0:
            interval_s = max(5.0, duration_s / 30.0)
        else:
            interval_s = max(20.0, duration_s / 90.0)
        thumb_count = max(1, min(120, int(math.ceil(duration_s / interval_s))))
        timestamps = [0.0] if duration_s <= 1.0 else [
            min(duration_s - 0.05, index * interval_s) for index in range(thumb_count)
        ]
        os.makedirs(thumb_dir, exist_ok=True)

        def build_waveform():
            waveform = []
            audio_path = _extract_waveform_audio(source, temp_root)
            waveform_duration = duration_s
            if audio_path and os.path.exists(audio_path):
                with wave.open(audio_path, "rb") as audio_file:
                    frame_count = audio_file.getnframes()
                    sample_rate = max(1, audio_file.getframerate())
                    raw_samples = audio_file.readframes(frame_count)
                samples = np.frombuffer(raw_samples, dtype=np.int16).astype(np.float32)
                waveform_duration = max(waveform_duration, frame_count / sample_rate)
                peak = float(np.max(np.abs(samples))) if samples.size else 0.0
                if peak > 0:
                    samples /= peak
                    bucket_count = int(min(1200, max(240, round(waveform_duration * 12.0))))
                    chunk_size = max(256, int(np.ceil(samples.size / max(1, bucket_count))))
                    for start in range(0, samples.size, chunk_size):
                        chunk = samples[start:start + chunk_size]
                        peak_value = float(np.max(np.abs(chunk))) if chunk.size else 0.0
                        rms_value = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
                        waveform.append(min(1.0, max(0.03, max(peak_value, rms_value * 1.15) ** 0.85)))
            return waveform, waveform_duration

        def build_thumbnail(index_and_time):
            index, timestamp_s = index_and_time
            output_path = os.path.join(thumb_dir, f"launcher_{digest}_v4_{index:03d}.jpg")
            if not os.path.exists(output_path):
                subprocess.run(
                    [_ffmpeg_path(), "-y", "-loglevel", "error", "-ss", f"{timestamp_s:.3f}",
                     "-i", source, "-frames:v", "1", "-q:v", "4",
                     "-vf", "scale=180:-1:force_original_aspect_ratio=decrease", output_path],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, timeout=20,
                    **subprocess_hidden_kwargs(),
                )
            return [float(timestamp_s), output_path] if os.path.exists(output_path) and os.path.getsize(output_path) > 0 else None

        # Two independent FFmpeg workers seek the original video directly,
        # while waveform extraction runs alongside them. This stays bounded
        # (two thumbnail processes plus one audio process) and avoids splits.
        from concurrent.futures import ThreadPoolExecutor
        import threading
        print(f"[Launcher] Preparing {thumb_count} timeline thumbnails with 2 workers + waveform worker")
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="capcap-thumbs") as thumbnail_pool:
            # Keep waveform CPU/audio work independent from the two thumbnail
            # slots so all three tasks can progress concurrently.
            waveform_result = []
            waveform_error = []
            def run_waveform():
                try:
                    waveform_result.extend(build_waveform())
                except Exception as exc:
                    waveform_error.append(exc)
            waveform_thread = threading.Thread(target=run_waveform, name="capcap-waveform", daemon=True)
            waveform_thread.start()
            thumbnails = [item for item in thumbnail_pool.map(build_thumbnail, enumerate(timestamps)) if item]
            waveform_thread.join()
        if waveform_error:
            raise waveform_error[0]
        waveform, duration_s = waveform_result if waveform_result else ([], duration_s)

        with open(manifest_path, "w", encoding="utf-8") as handle:
            json.dump({
                "visual_version": 4, "source": source, "size": int(stat.st_size), "mtime_ns": int(stat.st_mtime_ns),
                "duration_s": float(duration_s), "waveform": waveform, "thumbnails": thumbnails,
            }, handle)
        print(f"[Launcher] Timeline visuals prepared: waveform={len(waveform)}, thumbnails={len(thumbnails)}")
    except Exception as exc:
        print(f"[Launcher] Timeline visual preparation skipped: {exc}")


class LauncherWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.selected_video = ""
        self.selected_device = "cuda"
        self._thumbnail_dir = os.path.join(workspace_root(), "temp", "launcher_thumbs")

        from runtime_paths import asset_path
        from PySide6.QtGui import QIcon
        logo = asset_path("capcap.png")
        if os.path.exists(logo):
            self.setWindowIcon(QIcon(logo))

        self.setWindowTitle("CapCap - Video Translator")
        self.setMinimumSize(840, 540)
        self.setStyleSheet("""
            QDialog {
                background-color: #070d18;
                color: #e2e8f0;
                font-family: 'Segoe UI', sans-serif;
            }
            QScrollArea {
                background-color: #070d18;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background-color: #070d18;
            }
            QScrollBar:vertical {
                background: #0b1320;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #1c304a;
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: #38bdf8;
            }
        """)


        self._build_ui()
        QTimer.singleShot(0, self._load_recent)
        QTimer.singleShot(0, self._validate_resources_for_device)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        title = QLabel("CapCap V7")
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #ffffff;")
        subtitle = QLabel("Video Translation & Voiceover Studio")
        subtitle.setStyleSheet("font-size: 12px; color: #6ee7d6;")


        header_text = QVBoxLayout()
        header_text.addWidget(title)
        header_text.addWidget(subtitle)

        has_gpu, gpu_name, cuda_ready = self._detect_gpu_with_cuda()
        gpu_usable = has_gpu and cuda_ready
        self.selected_device = "cuda" if gpu_usable else "cpu"
        LauncherWindow._gpu_name = gpu_name if has_gpu else ""

        self._gpu_label = QLabel()
        header_text.addWidget(self._gpu_label)
        self._update_gpu_label(has_gpu, gpu_name, cuda_ready)

        self._missing_label = QLabel("", self)
        self._missing_label.setWordWrap(True)
        self._missing_label.setStyleSheet(
            "font-size: 11px; color: #ff6b6b; padding: 4px 8px;"
            " background-color: #3b1a1a; border: 1px solid #ff6b6b55; border-radius: 6px;"
        )
        self._missing_label.hide()
        header_text.addWidget(self._missing_label)

        device_row = QHBoxLayout()
        device_row.setSpacing(0)
        self.cpu_btn = QPushButton("CPU")
        self.cpu_btn.setCheckable(True)
        self.cpu_btn.setChecked(not gpu_usable)
        self.cpu_btn.setEnabled(True)
        self.gpu_btn = QPushButton("GPU (Recommended)" if gpu_usable else "GPU (N/A)")
        self.gpu_btn.setCheckable(True)
        self.gpu_btn.setChecked(gpu_usable)
        self.gpu_btn.setEnabled(gpu_usable)

        btn_style = """
            QPushButton {
                color: #8ea3bb; border: 1px solid #2f4868; padding: 3px 10px;
                font-size: 11px; font-weight: 600; border-radius: 0;
                background-color: transparent;
            }
            QPushButton:checked {
                background-color: #1a3a5c; color: #8ad7ff; border-color: #4ecdc4;
            }
            QPushButton:disabled {
                color: #445566; border-color: #1e3045;
            }
        """
        self.cpu_btn.setStyleSheet(btn_style + "QPushButton { border-top-left-radius: 6px; border-bottom-left-radius: 6px; }")
        self.gpu_btn.setStyleSheet(btn_style + "QPushButton { border-top-right-radius: 6px; border-bottom-right-radius: 6px; }")

        def _select_cpu(checked):
            if checked:
                self.gpu_btn.setChecked(False)
                self._set_selected_device("cpu")
                self._validate_resources_for_device()
            elif not self.gpu_btn.isChecked():
                self.cpu_btn.setChecked(True)

        def _select_gpu(checked):
            if checked:
                self.cpu_btn.setChecked(False)
                self._set_selected_device("cuda")
                self._validate_resources_for_device()
            elif not self.cpu_btn.isChecked():
                self.gpu_btn.setChecked(True)

        self.cpu_btn.clicked.connect(_select_cpu)
        self.gpu_btn.clicked.connect(_select_gpu)

        device_row.addWidget(self.cpu_btn)
        device_row.addWidget(self.gpu_btn)
        device_row.addStretch()
        header_text.addLayout(device_row)
        header.addLayout(header_text, 1)

        action_rows = QVBoxLayout()
        action_rows.setSpacing(6)
        action_row_one = QHBoxLayout()
        action_row_one.setSpacing(6)
        action_row_two = QHBoxLayout()
        action_row_two.setSpacing(6)

        self.new_btn = QPushButton("+ New Project")
        self.new_btn.setMinimumHeight(44)
        self.new_btn.setMinimumWidth(150)
        self.new_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ecdc4;
                color: #0a101e;
                font-weight: 700;
                font-size: 14px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #6ee7d6;
            }
        """)
        self.new_btn.clicked.connect(self._on_new_project)
        action_row_one.addWidget(self.new_btn)

        self.download_btn = QPushButton("Download from URL")

        self.download_btn.setMinimumHeight(44)
        self.download_btn.setMinimumWidth(140)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #1a3a5c;
                color: #38bdf8;
                font-weight: 700;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #2e5b88;
            }
            QPushButton:hover {
                background-color: #244b75;
                color: #7dd3fc;
                border-color: #38bdf8;
            }
        """)
        self.download_btn.setToolTip("Download video from YouTube, Douyin, TikTok, Bilibili, Facebook...")
        self.download_btn.clicked.connect(self._on_download_video)
        action_row_one.addWidget(self.download_btn)

        self.split_btn = QPushButton("Split Video")

        self.split_btn.setMinimumHeight(44)
        self.split_btn.setMinimumWidth(120)
        self.split_btn.setStyleSheet("""
            QPushButton {
                background-color: #22344d;
                color: #8ad7ff;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #34506f;
            }
            QPushButton:hover {
                background-color: #29405d;
            }
        """)
        self.split_btn.clicked.connect(self._on_split_video)
        action_row_one.addWidget(self.split_btn)

        self.resource_btn = QPushButton("Manage Resources")
        self.resource_btn.setMinimumHeight(44)
        self.resource_btn.setMinimumWidth(150)
        self.resource_btn.setStyleSheet("""
            QPushButton {
                background-color: #22344d;
                color: #8ad7ff;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #34506f;
            }
            QPushButton:hover {
                background-color: #29405d;
            }
        """)
        self.resource_btn.clicked.connect(self._on_manage_resources)
        action_row_one.addWidget(self.resource_btn)

        self.clean_video_btn = QPushButton("Clean Video Data")
        self.clean_video_btn.setMinimumHeight(44)
        self.clean_video_btn.setMinimumWidth(145)
        self.clean_video_btn.setStyleSheet("""
            QPushButton {
                background-color: #3a2630;
                color: #ffb3bd;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #70404e;
            }
            QPushButton:hover { background-color: #52303c; }
        """)
        self.clean_video_btn.setToolTip("Remove generated project data and video preview caches")
        self.clean_video_btn.clicked.connect(self._on_clean_video_data)
        action_row_two.addWidget(self.clean_video_btn)

        self.open_project_btn = QPushButton("Open Project Folder")
        self.open_project_btn.setMinimumHeight(44)
        self.open_project_btn.setMinimumWidth(165)
        self.open_project_btn.setStyleSheet("""
            QPushButton {
                background-color: #22344d;
                color: #8ad7ff;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #34506f;
            }
            QPushButton:hover { background-color: #29405d; }
        """)
        self.open_project_btn.setToolTip("Open the CapCap projects folder")
        self.open_project_btn.clicked.connect(self._on_open_project_folder)
        action_row_two.insertWidget(0, self.open_project_btn)

        self.about_btn = QPushButton("About / Help")
        self.about_btn.setMinimumHeight(44)
        self.about_btn.setMinimumWidth(145)
        self.about_btn.setStyleSheet("""
            QPushButton {
                background-color: #22344d;
                color: #8ad7ff;
                font-weight: 600;
                font-size: 13px;
                border-radius: 8px;
                border: 1px solid #34506f;
            }
            QPushButton:hover { background-color: #29405d; }
        """)
        self.about_btn.clicked.connect(self._on_about)
        action_row_two.addWidget(self.about_btn)
        action_row_two.addStretch()

        action_rows.addLayout(action_row_one)
        action_rows.addLayout(action_row_two)
        header.addLayout(action_rows)
        root.addLayout(header)

        self.section_label = QLabel("Recent Projects")
        self.section_label.setStyleSheet("font-size: 15px; font-weight: 700; color: #f8fafc;")
        root.addWidget(self.section_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea {
                background-color: #070d18;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background-color: #070d18;
            }
        """)

        self.grid_widget = QWidget()
        self.grid_widget.setStyleSheet("background-color: #070d18;")
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(4, 4, 4, 4)
        self.grid.setSpacing(16)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        scroll.setWidget(self.grid_widget)
        root.addWidget(scroll, 1)

        self.empty_box = QFrame()
        self.empty_box.setStyleSheet("""
            QFrame {
                background-color: #0c1524;
                border: 1px dashed #1e334e;
                border-radius: 12px;
                padding: 40px 20px;
            }
        """)
        empty_layout = QVBoxLayout(self.empty_box)
        empty_layout.setAlignment(Qt.AlignCenter)
        empty_layout.setSpacing(8)

        empty_title = QLabel("No Recent Projects")
        empty_title.setStyleSheet("color: #94a3b8; font-size: 14px; font-weight: 700;")
        empty_title.setAlignment(Qt.AlignCenter)

        empty_sub = QLabel("Click '+ New Project' or 'Download from URL' to start.")
        empty_sub.setStyleSheet("color: #475569; font-size: 12px;")
        empty_sub.setAlignment(Qt.AlignCenter)

        empty_layout.addWidget(empty_title)
        empty_layout.addWidget(empty_sub)
        root.addWidget(self.empty_box)
        self.empty_box.hide()


        self.loading_label = QLabel("Preparing video...")
        self.loading_label.setAlignment(Qt.AlignCenter)
        self.loading_label.setStyleSheet("color: #4ecdc4; font-size: 16px; font-weight: 700; padding: 20px;")
        self.loading_label.hide()
        root.addWidget(self.loading_label)

    def accept(self):
        if not self.selected_video or not os.path.exists(self.selected_video):
            super().accept()
            return

        try:
            service = self._resource_service()
            is_ok, missing = service.validate_device(self.selected_device)
            if not is_ok:
                from PySide6.QtWidgets import QMessageBox
                labels = [label for _rid, label in missing]
                if self.selected_device == "cpu":
                    prefix = "CPU mode needs:"
                else:
                    prefix = "GPU mode needs:"
                mb = QMessageBox(self)
                mb.setIcon(QMessageBox.Warning)
                mb.setWindowTitle("Missing Resources")
                mb.setText(f"{prefix}\n\n" + "\n".join(f"- {label}" for label in labels))
                mb.setInformativeText("Open Manage Resources to download them.")
                mb.addButton("Manage Resources", QMessageBox.AcceptRole)
                mb.addButton("Close", QMessageBox.RejectRole)
                mb.setStyleSheet(MSG_STYLE)
                mb.exec()
                self._validate_resources_for_device()
                return
        except Exception as exc:
            print(f"[Launcher] Resource validation failed: {exc}")

        duration = _get_video_duration(self.selected_video)
        MAX_DURATION = 7200
        if duration > MAX_DURATION:
            h = int(duration // 3600)
            m = int((duration % 3600) // 60)
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.warning(
                self, "Video Too Long",
                f"This video is {h}h {m}m long.\nCapCap works best with videos under 2 hours.\n\n"
                "Use 'Split Video' to cut it into 2-hour segments first.",
                QMessageBox.Ok,
            )
            reply.setStyleSheet(MSG_STYLE)
            return

        self._set_selected_device(self.selected_device)
        self.loading_label.show()
        self.loading_label.setText("Preparing thumbnails and waveform...\nLarge videos may continue preparing in the editor.")
        self.new_btn.setEnabled(False)
        self._extraction_done = False
        self._preprocess_started_at = time.monotonic()
        self._preprocess_continued_in_background = False
        import threading
        def _preprocess():
            from runtime_paths import workspace_root
            temp_root = os.path.join(workspace_root(), "temp")
            _prepare_timeline_visual_cache(self.selected_video, temp_root)
            self._extraction_done = True
        threading.Thread(target=_preprocess, daemon=True).start()
        self._loader_timer = QTimer()
        self._loader_timer.timeout.connect(self._on_loader_tick)
        self._loader_timer.start(200)

    def _on_loader_tick(self):
        if not getattr(self, "_extraction_done", False):
            # Do not hold the launcher hostage while a long video is being
            # sampled.  The cache worker is filesystem-only and can safely
            # finish after the editor opens; the editor has its own cache
            # consumers/fallback workers for any assets not ready yet.
            started = float(getattr(self, "_preprocess_started_at", 0.0) or 0.0)
            if started and time.monotonic() - started >= 12.0:
                self._preprocess_continued_in_background = True
                print("[Launcher] Timeline visual cache is still preparing; continuing in background.")
                self._loader_timer.stop()
                self._finish_accept()
            return
        self._loader_timer.stop()
        self._finish_accept()

    def _finish_accept(self):
        self.loading_label.hide()
        self.new_btn.setEnabled(True)
        self._save_device_env()
        super().accept()

    @staticmethod
    def _save_device_env():
        device = getattr(LauncherWindow, "_selected_device", "cuda")
        gpu_name = getattr(LauncherWindow, "_gpu_name", "")
        print(f"[Launcher] Saving CAPCAP_DEVICE={device}, GPU={gpu_name}")
        os.environ["CAPCAP_DEVICE"] = device
        os.environ["CAPCAP_GPU_NAME"] = gpu_name
        # ``__file__`` points inside _internal in a PyInstaller build. The
        # writable package root is the only place both later GUI launches and
        # the spawned worker can consistently read.
        env_path = os.path.join(workspace_root(), ".env")
        try:
            lines = []
            if os.path.exists(env_path):
                with open(env_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
            found = False
            for i, line in enumerate(lines):
                if line.startswith("CAPCAP_DEVICE="):
                    lines[i] = f"CAPCAP_DEVICE={device}\n"
                    found = True
                    break
            if not found:
                lines.append(f"CAPCAP_DEVICE={device}\n")
            with open(env_path, "w", encoding="utf-8") as f:
                f.writelines(lines)
        except Exception as e:
            print(f"[Launcher] Failed to write .env: {e}")

    def _set_selected_device(self, device: str) -> None:
        """Apply the launcher choice immediately and make it authoritative."""
        normalized = "cuda" if str(device or "").strip().lower() == "cuda" else "cpu"
        self.selected_device = normalized
        LauncherWindow._selected_device = normalized
        # The Main UI and its local worker inherit this exact value. Do not
        # wait for thumbnail preprocessing to finish before publishing it.
        os.environ["CAPCAP_DEVICE"] = normalized

    def _resource_service(self):
        from runtime_paths import workspace_root
        from services import ResourceDownloadService
        return ResourceDownloadService(workspace_root())

    def _validate_resources_for_device(self):
        try:
            service = self._resource_service()
        except Exception as exc:
            print(f"[Launcher] Failed to load resource service: {exc}")
            self.new_btn.setEnabled(True)
            return
        device = self.selected_device
        is_ok, missing = service.validate_device(device)
        self.new_btn.setEnabled(is_ok)
        if hasattr(self, "download_btn"):
            self.download_btn.setEnabled(is_ok)

        if device == "cuda":
            has_gpu = True
            gpu_name = ""
            try:
                import subprocess
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=10,
                    **subprocess_hidden_kwargs(),
                )
                if result.returncode == 0 and result.stdout.strip():
                    gpu_name = result.stdout.strip().split("\n")[0].strip()
            except Exception:
                pass
            cuda_ready = is_ok
            self._update_gpu_label(has_gpu, gpu_name, cuda_ready)
            if not cuda_ready:
                self.gpu_btn.setEnabled(False)
                self.gpu_btn.setText("GPU (N/A)")
        elif device == "cpu":
            has_gpu, _gpu_name, cuda_ready = self._detect_gpu_with_cuda()
            gpu_usable = has_gpu and cuda_ready
            if gpu_usable:
                self.gpu_btn.setEnabled(True)
                self.gpu_btn.setText("GPU (Recommended)")
                self._update_gpu_label(has_gpu, _gpu_name, cuda_ready)
        if is_ok:
            self._missing_label.hide()
            self._missing_label.setText("")
            if hasattr(self, "new_btn") and self.new_btn.toolTip():
                self.new_btn.setToolTip("")
        else:
            labels = [label for _rid, label in missing]
            if device == "cpu":
                prefix = "CPU mode needs:"
            else:
                prefix = "GPU mode needs:"
            text = f"{prefix} {', '.join(labels)}. Open Manage Resources to set them up."
            self._missing_label.setText(text)
            self._missing_label.show()
            self.new_btn.setToolTip(text)
        try:
            for i in range(self.grid.count()):
                item = self.grid.itemAt(i)
                if item is None:
                    continue
                widget = item.widget()
                if isinstance(widget, ProjectCard):
                    widget.setEnabled(is_ok)
        except Exception:
            pass

    def _load_recent(self):
        projects = _load_recent_projects()
        os.makedirs(self._thumbnail_dir, exist_ok=True)

        for i in reversed(range(self.grid.count())):
            item = self.grid.itemAt(i)
            if item:
                widget = item.widget()
                if widget:
                    widget.deleteLater()
                else:
                    self.grid.removeItem(item)

        existing = [p for p in projects if os.path.exists(p.get("video_path", ""))]
        if existing != projects:
            _save_recent_projects(None, existing)

        count = len(existing)
        if count == 0:
            self.section_label.setText("Recent Projects")
            if hasattr(self, "empty_box"):
                self.empty_box.show()
            return

        if hasattr(self, "empty_box"):
            self.empty_box.hide()
        self.section_label.setText(f"Recent Projects ({count})")

        available_w = max(300, self.grid_widget.width() - 10)
        card_w = 260
        spacing = 16
        columns = max(1, available_w // (card_w + spacing))

        for i, proj in enumerate(existing):
            card = ProjectCard(proj["video_path"], self._thumbnail_dir, self)
            row, col = divmod(i, columns)
            self.grid.addWidget(card, row, col, Qt.AlignTop | Qt.AlignLeft)


    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._load_recent)

    def _on_new_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Video", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)"
        )
        if path:
            self.selected_video = path
            self.accept()

    def _on_download_video(self):
        from views.video_download_dialog import VideoDownloadDialog
        dlg = VideoDownloadDialog(self)
        if dlg.exec() == QDialog.Accepted and dlg.downloaded_video_path:
            self.selected_video = dlg.downloaded_video_path
            self.accept()

    def _on_manage_resources(self):

        from views.resource_manager import open_resource_manager
        open_resource_manager(parent=self)
        self._validate_resources_for_device()

    def _on_open_project_folder(self):
        from PySide6.QtWidgets import QMessageBox
        projects_dir = os.path.join(workspace_root(), "projects")
        try:
            os.makedirs(projects_dir, exist_ok=True)
            if hasattr(os, "startfile"):
                os.startfile(projects_dir)
            else:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl.fromLocalFile(projects_dir))
        except Exception as exc:
            message = QMessageBox(QMessageBox.Warning, "Open Project Folder",
                f"Could not open the projects folder:\n\n{exc}", QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()

    def _on_clean_video_data(self):
        from PySide6.QtWidgets import QMessageBox

        confirm = QMessageBox(QMessageBox.Warning, "Clean Video Data",
            "Remove all generated project data and video preview caches?\n\n"
            "Source videos, downloaded models, Piper voices, CUDA files, and application resources will not be touched.",
            QMessageBox.Yes | QMessageBox.No, self)
        confirm.setStyleSheet(MSG_STYLE)
        if confirm.exec() != QMessageBox.Yes:
            return

        # Keep generated project/cache data in the explicit writable runtime
        # root rather than deriving it from a module location.
        root = workspace_root()
        targets = [
            os.path.join(root, "projects"),
            os.path.join(root, "temp"),
        ]

        # Project cards can still own loaded thumbnail pixmaps from temp.
        # Detach them and process their deferred deletion before removing the
        # cache tree; this avoids a common first-click Windows file lock.
        try:
            from PySide6.QtWidgets import QApplication
            for index in reversed(range(self.grid.count())):
                item = self.grid.takeAt(index)
                widget = item.widget() if item is not None else None
                if widget is not None:
                    widget.setParent(None)
                    widget.deleteLater()
            QApplication.processEvents()
        except Exception:
            pass

        removed = 0
        errors = []
        for target in targets:
            if not os.path.exists(target):
                continue
            last_error = None
            # FFmpeg/thumbnail work can release a file just after the user
            # confirms cleanup. Retry briefly instead of making the user
            # click Clean Video Data a second time.
            for attempt in range(5):
                try:
                    shutil.rmtree(target)
                    removed += 1
                    last_error = None
                    break
                except FileNotFoundError:
                    last_error = None
                    break
                except OSError as exc:
                    last_error = exc
                    if attempt < 4:
                        try:
                            QApplication.processEvents()
                        except Exception:
                            pass
                        time.sleep(0.25 * (attempt + 1))
            if last_error is not None:
                errors.append(f"{os.path.basename(target)}: {last_error}")
        for target in targets:
            try:
                os.makedirs(target, exist_ok=True)
            except OSError:
                pass
        # Cleaning all generated project data also resets the launcher history;
        # no deleted project should remain listed in recent_projects.json.
        try:
            _save_recent_projects(None, [])
            self._load_recent()
        except Exception as exc:
            errors.append(f"recent projects: {exc}")

        if errors:
            detail = "\n".join(errors)
            message = QMessageBox(QMessageBox.Warning, "Clean Video Data",
                f"Some data could not be removed:\n\n{detail}", QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()
        else:
            message = QMessageBox(QMessageBox.Information, "Clean Video Data",
                "Generated project data and video caches were cleared.", QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()

    def _on_about(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices, QPixmap
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle("About CapCap")
        dialog.setMinimumSize(650, 650)
        dialog.setStyleSheet("QDialog { background: #0a101e; color: #d7e3f4; }")
        layout = QVBoxLayout(dialog)
        title = QLabel("CapCap V7 — Tool Information", dialog)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        layout.addWidget(title)

        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            "QTextBrowser { background: #0f1928; color: #d7e3f4; border: 1px solid #1e3045; "
            "border-radius: 8px; padding: 10px; }"
        )
        browser.setHtml("""
        <h3 style='color:#8ad7ff;'>Description</h3>
        <p>CapCap is a Windows application that supports both CPU and GPU processing.</p>
        <p>GPU mode provides the best overall experience and performance. GPU acceleration currently supports NVIDIA GPUs.</p>
        <p>If CUDA is not detected correctly, first update your NVIDIA GPU driver. If needed, install CUDA 12.4 from:<br>
        <a href='https://developer.nvidia.com/cuda-12-4-0-download-archive'>CUDA 12.4 Download Archive</a></p>

        <h3 style='color:#8ad7ff;'>Tutorial / Resource Setup</h3>
        <p>Download the resource, then place it in the matching CapCap folder:</p>
        <table cellspacing='6'>
        <tr><td><b>Whisper models</b></td><td><code>CapCap\\models\\faster_whisper</code></td></tr>
        <tr><td><b>CUDA / cuDNN runtime</b></td><td><code>CapCap\\bin\\cuda12_fw</code></td></tr>
        <tr><td><b>SenseVoice</b></td><td>Bundled by default in <code>CapCap\\models\\sensevoice</code></td></tr>
        <tr><td><b>RapidOCR models</b></td><td>Bundled by default; optional files use <code>CapCap\\rapidocr\\models</code></td></tr>
        <tr><td><b>Piper voices</b></td><td><code>CapCap\\models\\piper</code> (Vietnamese) or <code>CapCap\\models\\piper-en</code> (English)</td></tr>
        <tr><td><b>Speaker Detection</b></td><td><code>CapCap\\models\\pyannote</code></td></tr>
        </table>
        <p>Resource Manager provides download links for supported optional resources. Extract downloaded archives into the folder shown above.</p>

        <h3 style='color:#8ad7ff;'>How to Setup</h3>
        <p>CapCap has two processing modes: <b>CPU Mode</b> and <b>GPU Mode</b>.</p>
        <p><b>CPU Mode:</b> Ready to use immediately without additional downloads. Optional resources add more models, voices, or features.</p>
        <p><b>GPU Mode:</b> Requires the <b>GPU Acceleration Pack</b>. Download and extract it into <code>CapCap\\bin</code>. Whisper Medium is optional but recommended for better GPU transcription quality.</p>
        <p>Other resources are optional enhancements. CapCap works without them unless you select a feature that needs one.</p>

        <h3 style='color:#8ad7ff;'>How to Use</h3>
        <p><b>Left side:</b> Workflow progress, configuration, and options.</p>
        <p><b>Right side — Top:</b> Video Preview and action buttons on the left; the selected Timeline layer's Inspector on the right.</p>
        <p><b>Right side — Bottom:</b> Timeline Editor and timeline editing actions.</p>
        <ol>
        <li>Use the setup guidance above and download any resources you need.</li>
        <li>Open Settings and select the Subtitle Source and AI Translation provider.</li>
        <li>In Language, select the input and output languages.</li>
        <li>Click <b>Generate</b>: choose <b>Full Pipeline</b> to run automatically, or <b>Step-by-Step</b> for individual phase control.</li>
        </ol>

        <h3 style='color:#8ad7ff;'>Developer Information</h3>
        <p>GitHub: <a href='https://github.com/notepower2k1/CapCap'>github.com/notepower2k1/CapCap</a></p>
        """)
        layout.addWidget(browser, 1)

        donation_row = QHBoxLayout()
        donation_row.setSpacing(18)
        donation_label = QLabel("Donate Vietnam\nScan to support development", dialog)
        donation_label.setStyleSheet("color:#d7e3f4; font-weight:600;")
        qr_label = QLabel(dialog)
        qr_label.setAlignment(Qt.AlignCenter)
        qr_path = asset_path("qr.png")
        qr_pixmap = QPixmap(qr_path)
        if not qr_pixmap.isNull():
            qr_label.setPixmap(qr_pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            qr_label.setText("QR unavailable")
        donation_row.addWidget(donation_label)
        donation_row.addWidget(qr_label)
        donation_row.addStretch()

        coffee_group = QHBoxLayout()
        coffee_group.setSpacing(5)
        coffee_text = QLabel("International Donation\nClick to Buy Me a Coffee", dialog)
        coffee_text.setStyleSheet("color:#d7e3f4; font-weight:600;")
        coffee_group.addWidget(coffee_text)
        coffee_path = asset_path("buymeacoffee.png")
        coffee_pixmap = QPixmap(coffee_path)
        coffee_image = QLabel(dialog)
        coffee_image.setAlignment(Qt.AlignCenter)
        if not coffee_pixmap.isNull():
            coffee_image.setPixmap(coffee_pixmap.scaled(190, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            coffee_image.setText("Buy Me a Coffee image unavailable")
        coffee_image.setToolTip("Open Buy Me a Coffee")
        coffee_image.setCursor(Qt.PointingHandCursor)
        coffee_image.setAccessibleName("International Donation - Buy Me a Coffee")
        coffee_image.mousePressEvent = lambda _event: QDesktopServices.openUrl(QUrl("https://buymeacoffee.com/hcaht"))
        coffee_group.addWidget(coffee_image)
        donation_row.addLayout(coffee_group)
        layout.addLayout(donation_row)
        buttons = QDialogButtonBox(QDialogButtonBox.Close, parent=dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def _on_split_video(self):
        from PySide6.QtWidgets import QMessageBox, QProgressDialog, QInputDialog
        from PySide6.QtCore import QThread, Signal
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Long Video to Split", "",
            "Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)"
        )
        if not path:
            return

        duration = _get_video_duration(path)
        if duration <= 7200:
            mb = QMessageBox(QMessageBox.Information, "No Split Needed",
                "This video is under 2 hours. You can open it directly with '+ New Project'.",
                QMessageBox.Ok, self)
            mb.setStyleSheet(MSG_STYLE)
            mb.exec()
            return

        h = int(duration // 3600)
        m = int((duration % 3600) // 60)

        seg_minutes, ok = QInputDialog.getInt(
            self, "Segment Duration",
            f"Video is {h}h {m}m.\nSplit into segments of how many minutes?",
            120, 10, 1440, 10,
        )
        if not ok:
            return

        seg_seconds = seg_minutes * 60
        base, ext = os.path.splitext(path)
        out_pattern = f"{base}_part%03d{ext}"

        reply = QMessageBox(QMessageBox.Question, "Confirm Split",
            f"Split into {seg_minutes}-minute segments using stream copy (no re-encode, fast).\n\n"
            f"Output: {out_pattern}\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No, self)
        reply.setStyleSheet(MSG_STYLE)
        if reply.exec() != QMessageBox.Yes:
            return

        progress = QProgressDialog("Splitting video...", None, 0, 0, self)
        progress.setWindowTitle("Split Video")
        progress.setModal(True)
        progress.setCancelButton(None)
        progress.show()

        import subprocess
        import threading

        def _do_split():
            try:
                subprocess.run(
                    [_ffmpeg_path(), "-y", "-i", path, "-c", "copy",
                     "-f", "segment", "-segment_time", str(seg_seconds),
                     "-reset_timestamps", "1", out_pattern],
                    capture_output=True, timeout=3600, **subprocess_hidden_kwargs(),
                )
                progress.accept()
            except Exception as e:
                progress.accept()
                print(f"[Split] Error: {e}")

        threading.Thread(target=_do_split, daemon=True).start()
        progress.exec()

        mb = QMessageBox(QMessageBox.Information, "Done",
            f"Video split into {seg_minutes}-minute segments.\nSaved alongside the original file.",
            QMessageBox.Ok, self)
        mb.setStyleSheet(MSG_STYLE)
        mb.exec()

    def _detect_gpu_with_cuda(self):
        has_gpu = False
        gpu_name = ""
        cuda_ready = False
        try:
            import subprocess
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_name = result.stdout.strip().split("\n")[0].strip()
                has_gpu = True
        except Exception:
            pass
        if not has_gpu:
            try:
                import torch
                if torch.cuda.is_available():
                    name = torch.cuda.get_device_name(0)
                    vram = torch.cuda.get_device_properties(0).total_mem // (1024 ** 3)
                    gpu_name = f"{name} ({vram}GB)"
                    has_gpu = True
            except Exception:
                pass
        if has_gpu:
            try:
                service = self._resource_service()
                cuda_ready = service.is_requirement_met("cuda:whisper")
            except Exception:
                pass
        return has_gpu, gpu_name, cuda_ready

    def _update_gpu_label(self, has_gpu: bool, gpu_name: str, cuda_ready: bool):
        if has_gpu:
            if cuda_ready:
                self._gpu_label.setText(f"GPU: {gpu_name}  \u2713 CUDA ready")
                self._gpu_label.setStyleSheet("font-size: 11px; color: #4ecdc4;")
            else:
                self._gpu_label.setText(f"GPU: {gpu_name}  \u2717 Need GPU Acceleration Pack")
                self._gpu_label.setStyleSheet("font-size: 11px; color: #ffa500;")
        else:
            self._gpu_label.setText("CPU only")
            self._gpu_label.setStyleSheet("font-size: 11px; color: #5a7a9a;")

    @staticmethod
    def add_recent(settings_or_none, video_path: str):
        video_path = os.path.normpath(video_path)
        projects = _load_recent_projects()
        projects = [p for p in projects if os.path.exists(p.get("video_path", ""))]
        existing = [p for p in projects if os.path.normpath(p.get("video_path", "")) == video_path]
        if existing:
            projects.remove(existing[0])
        projects.insert(0, {
            "video_path": video_path,
            "opened_at": int(time.time()),
        })
        projects = projects[:12]
        _save_recent_projects(None, projects)


def _thumbnail_name(video_path: str) -> str:
    import hashlib
    h = hashlib.md5(video_path.encode()).hexdigest()
    return f"{h}.jpg"


def show_launcher(settings_or_none) -> str:
    """Show launcher, return selected video path or empty string."""
    w = LauncherWindow()
    if w.exec() == QDialog.Accepted:
        return w.selected_video
    return ""
