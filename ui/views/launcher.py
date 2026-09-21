import hashlib
import math
import os
import json
import time
import shutil
import hashlib
import re

import threading

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from runtime_paths import asset_path, subprocess_hidden_kwargs, workspace_root

try:
    from i18n import get_language, language_items, localize_widget_tree, set_language, t
except ImportError:
    from ui.i18n import get_language, language_items, localize_widget_tree, set_language, t



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

    try:
        from app.video_processor import get_video_duration
        return float(get_video_duration(video_path) or 0.0)
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
    thumb_ready = Signal(object)

    def __init__(self, video_path: str, thumbnail_cache_dir: str, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self._orig_pixmap = None
        self.thumb_ready.connect(self._on_thumb_ready)
        self.setObjectName("statusCard")
        self.setMinimumSize(180, 184)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("ProjectCard:hover { border: 2px solid #4ecdc4; }")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.thumb_label = QLabel()
        self.thumb_label.setMinimumSize(160, 120)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet("background-color: #0d1220; border-radius: 6px;")
        self.thumb_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout.addWidget(self.thumb_label)

        self.name_label = QLabel(os.path.basename(video_path))
        self.name_label.setWordWrap(True)
        self.name_label.setMaximumHeight(36)
        self.name_label.setStyleSheet("color: #e0e0e0; font-size: 11px; font-weight: 600;")
        layout.addWidget(self.name_label)

        stage_text, stage_color = _project_pipeline_status(video_path)
        self.stage_badge = QLabel(t(stage_text))
        self.stage_badge.setAlignment(Qt.AlignCenter)
        self.stage_badge.setStyleSheet(
            f"background-color: #142437; color: {stage_color}; border: 1px solid #2e4b68; "
            "border-radius: 7px; padding: 3px 7px; font-size: 10px; font-weight: 700;"
        )
        layout.addWidget(self.stage_badge)

        self._load_thumb(thumbnail_cache_dir)

    def _load_thumb(self, cache_dir):
        thumb_path = os.path.join(cache_dir, _thumbnail_name(self.video_path))
        if os.path.exists(thumb_path):
            self._orig_pixmap = QPixmap(thumb_path)
            self._update_thumb()
        else:
            self.thumb_label.setText(t("No Preview"))
            self._start_async_thumb_extraction(thumb_path)

    def _start_async_thumb_extraction(self, thumb_path: str):
        video_path = self.video_path
        if not video_path or not os.path.exists(video_path):
            return

        def _extract():
            try:
                import numpy as np
                from app.media_decode import iter_video_thumbnails
                for _pts, rgb in iter_video_thumbnails(video_path, [0.0], width=320):
                    h, w, _ = rgb.shape
                    rgb_contig = np.ascontiguousarray(rgb)
                    qimg = QImage(rgb_contig.data, w, h, w * 3, QImage.Format_RGB888).copy()
                    self.thumb_ready.emit(qimg)
                    return
            except Exception:
                pass
            try:
                res = _extract_thumbnail(video_path, thumb_path)
                if res and os.path.exists(res):
                    self.thumb_ready.emit(res)
            except Exception:
                pass

        t = threading.Thread(target=_extract, name="launcher-card-thumb", daemon=True)
        t.start()

    def _on_thumb_ready(self, item):
        if isinstance(item, QImage):
            self._orig_pixmap = QPixmap.fromImage(item)
        elif isinstance(item, str) and os.path.exists(item):
            self._orig_pixmap = QPixmap(item)
        self._update_thumb()

    def _update_thumb(self):
        if self._orig_pixmap is None or self._orig_pixmap.isNull():
            return
        w = self.thumb_label.width()
        if w > 0:
            self.thumb_label.setPixmap(self._orig_pixmap.scaled(w, 120, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_thumb()

    def mousePressEvent(self, event):
        if not self.isEnabled():
            event.ignore()
            return
        self.window().selected_video = self.video_path
        self.window().accept()


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
             "-vn", "-af", "aresample=async=1", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", audio_path],
            check=True, timeout=60, **subprocess_hidden_kwargs(),
        )
        print(f"[Launcher] Waveform audio extracted: {audio_path}")
    except Exception as exc:
        print(f"[Launcher] Waveform extract failed: {exc}")
        return ""
    return audio_path if os.path.exists(audio_path) else ""


def _prepare_timeline_visual_cache(video_path: str, temp_root: str, progress_cb=None) -> None:
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

        if callable(progress_cb):
                progress_cb(t("Checking project cache..."), 20)

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
                if callable(progress_cb):
                    progress_cb(t("Project cache ready!"), 100)
                return
        except (OSError, ValueError, TypeError):
            pass

        duration_s = _get_video_duration(source)
        max_visual_dur = float(os.environ.get("CAPCAP_TIMELINE_VISUALS_MAX_DURATION", 3600.0))
        if duration_s > max_visual_dur:
            print(f"[Launcher] Video duration ({duration_s:.1f}s > {max_visual_dur:.0f}s): skipping precomputed visual cache.")
            manifest = {
                "visual_version": 4,
                "source": source,
                "size": int(stat.st_size),
                "mtime_ns": int(getattr(stat, "st_mtime_ns", 0)),
                "duration_s": duration_s,
                "waveform": [],
                "thumbnails": [],
            }
            try:
                with open(manifest_path, "w", encoding="utf-8") as handle:
                    json.dump(manifest, handle, indent=2)
            except OSError:
                pass
            if callable(progress_cb):
                progress_cb(t("Ready to open project!"), 100)
            return

        if callable(progress_cb):
            progress_cb(t("Extracting timeline waveform and thumbnails..."), 45)

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

        # Try native in-process thumbnail and waveform generation
        native_done = False
        try:
            from app.media_decode import build_waveform as native_build_waveform

            native_wf, native_dur = native_build_waveform(source)
            waveform = native_wf
            duration_s = max(duration_s, native_dur)
            # In native mode, thumbnails are generated in RAM on-demand by TimelineThumbnailWorker.
            # We preserve existing old JPG cache if available, but never generate new JPGs on disk.
            existing_thumbs = existing.get("thumbnails", []) if "existing" in locals() and isinstance(existing, dict) else []
            thumbnails = [t for t in existing_thumbs if os.path.exists(t[1])] if existing_thumbs else []
            native_done = True
        except Exception as ex:
            print(f"[Launcher] Native visual prep error, falling back to FFmpeg: {ex}")
            native_done = False

        if not native_done:
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

            from concurrent.futures import ThreadPoolExecutor
            import threading
            print(f"[Launcher] Preparing {thumb_count} timeline thumbnails with 2 workers + waveform worker")
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="capcap-thumbs") as thumbnail_pool:
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
        if callable(progress_cb):
            progress_cb(t("Ready to open project!"), 100)
    except Exception as exc:
        print(f"[Launcher] Timeline visual preparation skipped: {exc}")


class VisualCacheWorker(QThread):
    progress = Signal(str, int)
    finished_prep = Signal()

    def __init__(self, target_video: str, temp_root: str, parent=None):
        super().__init__(parent)
        self.target_video = str(target_video or "")
        self.temp_root = str(temp_root or "")

    def run(self):
        def _on_progress(status, pct):
            self.progress.emit(str(status), int(pct))
        try:
            _prepare_timeline_visual_cache(self.target_video, self.temp_root, progress_cb=_on_progress)
        except Exception as exc:
            print(f"[Launcher] Visual cache preparation error: {exc}")
        self.progress.emit(t("Ready to open project!"), 100)
        self.finished_prep.emit()


class LauncherWindow(QDialog):
    def __init__(self):
        super().__init__()
        set_language(get_language())
        self.selected_video = ""
        self.selected_device = "cuda"
        self._thumbnail_dir = os.path.join(workspace_root(), "temp", "launcher_thumbs")

        from runtime_paths import asset_path
        from utils.display_utils import build_contrasting_window_icon
        ico = asset_path("capcap.ico")
        logo = ico if os.path.exists(ico) else asset_path("capcap.png")
        if os.path.exists(logo):
            self.setWindowIcon(build_contrasting_window_icon(logo, is_dark_bg=True))

        self.setWindowTitle(t("CapCap - Video Translator"))
        self.setMinimumSize(840, 540)
        self.setStyleSheet("""
            QDialog {
                background-color: #0a101e;
                color: #cfe6ff;
            }
            #statusCard {
                background-color: #0f1928;
                border: 1px solid #1e3045;
                border-radius: 8px;
            }
        """)
        try:
            from utils.display_utils import apply_windows_dark_title_bar
            apply_windows_dark_title_bar(self)
        except Exception:
            pass

        self._build_ui()
        QTimer.singleShot(0, self._load_recent)
        QTimer.singleShot(0, self._validate_resources_for_device)

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 24, 24, 24)
        root.setSpacing(16)

        header = QHBoxLayout()
        self._title_label = QLabel("CapCap V8")
        title = self._title_label
        title.setStyleSheet("font-size: 26px; font-weight: 800; color: #ffffff;")
        self._subtitle_label = QLabel("Video Translation & Voiceover Studio")
        subtitle = self._subtitle_label
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

        language_row = QHBoxLayout()
        self._language_label = QLabel("Language")
        self._language_label.setStyleSheet("color: #8ad7ff; font-size: 12px; font-weight: 700;")
        self.language_combo = QComboBox()
        self.language_combo.setObjectName("languageSelector")
        self.language_combo.setMinimumWidth(125)
        for label, code in language_items():
            self.language_combo.addItem(label, code)
        self.language_combo.setCurrentIndex(
            max(0, self.language_combo.findData(get_language()))
        )
        self.language_combo.currentIndexChanged.connect(self._on_language_changed)
        language_row.addWidget(self._language_label)
        language_row.addWidget(self.language_combo)
        language_row.addStretch()
        header_text.addLayout(language_row)

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
        self.section_label.setStyleSheet("font-size: 14px; font-weight: 700; color: #8ad7ff;")
        root.addWidget(self.section_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        self.grid_widget = QWidget()
        self.grid = QGridLayout(self.grid_widget)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(12)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.grid_widget)
        root.addWidget(self.scroll, 1)

        self.empty_label = QLabel("No recent projects. Click \"+ New Project\" to start.")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setStyleSheet("color: #556677; font-size: 13px;")
        self.empty_label.hide()
        root.addWidget(self.empty_label)

        # Smooth Loading Overlay Panel
        self.loading_panel = QFrame(self)
        self.loading_panel.setObjectName("loadingPanel")
        self.loading_panel.setMinimumWidth(720)
        self.loading_panel.setMaximumWidth(900)
        self.loading_panel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.loading_panel.setStyleSheet("""
            #loadingPanel {
                background-color: #0c1524;
                border: 1px solid #1c334d;
                border-radius: 16px;
            }
        """)
        panel_layout = QVBoxLayout(self.loading_panel)
        panel_layout.setContentsMargins(36, 32, 36, 32)
        panel_layout.setSpacing(16)

        self.loading_title = QLabel("Opening Project...")
        self.loading_title.setStyleSheet("font-size: 20px; font-weight: 700; color: #ffffff;")
        panel_layout.addWidget(self.loading_title)

        self.loading_file_label = QLabel("")
        self.loading_file_label.setStyleSheet("font-size: 14px; color: #8ad7ff; font-weight: 600;")
        self.loading_file_label.setWordWrap(True)
        panel_layout.addWidget(self.loading_file_label)

        self.loading_bar = QProgressBar()
        self.loading_bar.setFixedHeight(10)
        self.loading_bar.setTextVisible(False)
        self.loading_bar.setRange(0, 100)
        self.loading_bar.setValue(10)
        self.loading_bar.setStyleSheet("""
            QProgressBar {
                background-color: #060a12;
                border: 1px solid #162638;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4ecdc4, stop:1 #6ee7d6);
                border-radius: 4px;
            }
        """)
        panel_layout.addWidget(self.loading_bar)

        self.loading_status_label = QLabel("Preparing workspace...")
        self.loading_status_label.setStyleSheet("font-size: 13px; color: #6ee7d6; font-weight: 600;")
        panel_layout.addWidget(self.loading_status_label)

        self.loading_container = QWidget(self)
        loading_center_layout = QVBoxLayout(self.loading_container)
        loading_center_layout.setContentsMargins(24, 0, 24, 0)
        loading_center_layout.addStretch(1)
        loading_center_layout.addWidget(self.loading_panel, 0, Qt.AlignCenter)
        loading_center_layout.addStretch(1)
        self.loading_container.hide()
        root.addWidget(self.loading_container, 1)
        self._retranslate_ui()

    def _on_language_changed(self, index: int):
        code = self.language_combo.itemData(index)
        if not code:
            return
        set_language(code)
        self._retranslate_ui()

    def _retranslate_ui(self):
        """Refresh launcher copy and cards after an immediate language change."""
        localize_widget_tree(self)
        self.setWindowTitle(t("CapCap - Video Translator"))
        self._title_label.setText(t("CapCap V8"))
        self._subtitle_label.setText(t("Video Translation & Voiceover Studio"))
        self._language_label.setText(t("Language"))
        self.cpu_btn.setText(t("CPU"))
        self._update_gpu_label(
            bool(getattr(self, "_last_has_gpu", False)),
            str(getattr(self, "_last_gpu_name", "")),
            bool(getattr(self, "_last_cuda_ready", False)),
        )
        self.new_btn.setText(t("+ New Project"))
        self.split_btn.setText(t("Split Video"))
        self.resource_btn.setText(t("Manage Resources"))
        self.clean_video_btn.setText(t("Clean Video Data"))
        self.clean_video_btn.setToolTip(t("Remove generated project data and video preview caches"))
        self.open_project_btn.setText(t("Open Project Folder"))
        self.open_project_btn.setToolTip(t("Open the CapCap projects folder"))
        self.about_btn.setText(t("About / Help"))
        self.section_label.setText(t("Recent Projects"))
        self.empty_label.setText(t('No recent projects. Click "+ New Project" to start.'))
        self.loading_title.setText(t("Opening Project..."))
        self.loading_status_label.setText(t("Preparing workspace..."))
        self._load_recent()
        if hasattr(self, "_missing_label"):
            self._validate_resources_for_device()

    def set_project_loader(self, loader):
        self._project_loader = loader

    def show_loading(self, video_path: str):
        if hasattr(self, "scroll"):
            self.scroll.hide()
        if hasattr(self, "section_label"):
            self.section_label.hide()
        if hasattr(self, "empty_label"):
            self.empty_label.hide()
        for btn in (
            getattr(self, "new_btn", None),
            getattr(self, "split_btn", None),
            getattr(self, "resource_btn", None),
            getattr(self, "clean_video_btn", None),
            getattr(self, "open_project_btn", None),
            getattr(self, "about_btn", None),
            getattr(self, "cpu_btn", None),
            getattr(self, "gpu_btn", None),
            getattr(self, "language_combo", None),
        ):
            if btn is not None:
                btn.setEnabled(False)
        self.loading_file_label.setText(os.path.basename(video_path))
        self.loading_bar.setValue(10)
        self.loading_status_label.setText(t("Preparing environment and hardware..."))
        self.loading_container.show()
        self.loading_panel.show()
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def update_loading_progress(self, status: str, value: int):
        if hasattr(self, "loading_status_label"):
            self.loading_status_label.setText(status)
        if hasattr(self, "loading_bar"):
            self.loading_bar.setValue(max(0, min(100, int(value))))
        from PySide6.QtWidgets import QApplication
        QApplication.processEvents()

    def accept(self):
        if not self.selected_video or not os.path.exists(self.selected_video):
            super().accept()
            return

        try:
            service = self._resource_service()
            is_ok, missing, _advisory = self._launch_resource_state(service)
            if not is_ok:
                from PySide6.QtWidgets import QMessageBox
                labels = [t(label) for _rid, label in missing]
                if self.selected_device == "cpu":
                    prefix = t("CPU mode needs:")
                else:
                    prefix = t("GPU mode needs:")
                mb = QMessageBox(self)
                mb.setIcon(QMessageBox.Warning)
                mb.setWindowTitle(t("Missing Resources"))
                mb.setText(f"{prefix}\n\n" + "\n".join(f"- {label}" for label in labels))
                mb.setInformativeText(t("Open Manage Resources to download them."))
                mb.addButton(t("Manage Resources"), QMessageBox.AcceptRole)
                mb.addButton(t("Close"), QMessageBox.RejectRole)
                mb.setStyleSheet(MSG_STYLE)
                mb.exec()
                self._validate_resources_for_device()
                return
        except Exception as exc:
            print(f"[Launcher] Resource validation failed: {exc}")

        self._set_selected_device(self.selected_device)
        self._save_device_env()
        self.show_loading(self.selected_video)

        from runtime_paths import workspace_root
        temp_root = os.path.join(workspace_root(), "temp")

        self._cache_worker = VisualCacheWorker(self.selected_video, temp_root, self)
        self._cache_worker.progress.connect(self.update_loading_progress)

        self._prep_timeout_timer = QTimer(self)
        self._prep_timeout_timer.setSingleShot(True)
        self._prep_timeout_timer.timeout.connect(self._on_prep_timeout)
        self._prep_timeout_timer.start(15000)

        self._cache_worker.finished_prep.connect(self._on_visual_cache_done)
        self._cache_worker.start()

    def _on_visual_cache_done(self):
        if hasattr(self, "_prep_timeout_timer"):
            self._prep_timeout_timer.stop()
        worker = getattr(self, "_cache_worker", None)
        if worker is not None and worker.isRunning():
            worker.wait(2000)
        QTimer.singleShot(50, self._finish_accept)

    def _on_prep_timeout(self):
        print("[Launcher] Visual cache preparation timed out; continuing to editor.")
        worker = getattr(self, "_cache_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.requestInterruption()
                worker.wait(1000)
            except Exception:
                pass
        self._finish_accept()

    def _finish_accept(self):
        super().accept()

    def closeEvent(self, event):
        worker = getattr(self, "_cache_worker", None)
        if worker is not None and worker.isRunning():
            try:
                worker.requestInterruption()
                worker.wait(1000)
            except Exception:
                pass
        super().closeEvent(event)

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

    def _launch_resource_state(self, service):
        """Return launch-blocking and advisory resource requirements.

        SenseVoice has a recovery download for installations where the
        bundled model cannot be detected, but its absence must not prevent a
        user from opening the Main UI.  The Generate/prepare workflow still
        performs the exact runtime validation before transcription starts.
        GPU requirements such as the CUDA pack remain launch-blocking.
        """
        _validated, missing = service.validate_device(self.selected_device)
        blocking = []
        advisory = []
        for resource_id, label in missing:
            if str(resource_id or "").strip().lower().startswith("sensevoice:"):
                advisory.append((resource_id, label))
            else:
                blocking.append((resource_id, label))
        return (not blocking), blocking, advisory

    def _validate_resources_for_device(self):
        try:
            service = self._resource_service()
        except Exception as exc:
            print(f"[Launcher] Failed to load resource service: {exc}")
            self.new_btn.setEnabled(True)
            return
        device = self.selected_device
        is_ok, missing, advisory = self._launch_resource_state(service)
        self.new_btn.setEnabled(is_ok)
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
                self.gpu_btn.setText(t("GPU (N/A)"))
        elif device == "cpu":
            has_gpu, _gpu_name, cuda_ready = self._detect_gpu_with_cuda()
            gpu_usable = has_gpu and cuda_ready
            if gpu_usable:
                self.gpu_btn.setEnabled(True)
                self.gpu_btn.setText(t("GPU (Recommended)"))
                self._update_gpu_label(has_gpu, _gpu_name, cuda_ready)
        if is_ok and not advisory:
            self._missing_label.hide()
            self._missing_label.setText("")
            if hasattr(self, "new_btn") and self.new_btn.toolTip():
                self.new_btn.setToolTip("")
        elif is_ok:
            labels = [t(label) for _rid, label in advisory]
            text = t(
                "SenseVoice is not detected yet. You can continue to the Main UI; "
                "download SenseVoice from Manage Resources before using it for transcription."
            )
            self._missing_label.setText(text)
            self._missing_label.show()
            self.new_btn.setToolTip(text)
        else:
            labels = [t(label) for _rid, label in missing]
            if advisory:
                labels.extend(t(label) for _rid, label in advisory)
            if device == "cpu":
                prefix = t("CPU mode needs:")
            else:
                prefix = t("GPU mode needs:")
            text = t(
                "{prefix} {labels}. Open Manage Resources to set them up.",
                prefix=prefix,
                labels=", ".join(labels),
            )
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
            widget = self.grid.itemAt(i).widget()
            if widget:
                widget.deleteLater()

        existing = [p for p in projects if os.path.exists(p.get("video_path", ""))]
        if existing != projects:
            _save_recent_projects(None, existing)

        if not existing:
            self.empty_label.show()
            return
        self.empty_label.hide()

        columns = min(3, max(1, (self.grid_widget.width() - 24) // 242))
        for i, proj in enumerate(existing):
            card = ProjectCard(proj["video_path"], self._thumbnail_dir, self)
            row, col = divmod(i, max(1, columns))
            self.grid.addWidget(card, row, col)
            self.grid.setColumnStretch(col, 1)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        QTimer.singleShot(0, self._load_recent)

    def _on_new_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, t("Select Video"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)")
        )
        if path:
            self.selected_video = path
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
            message = QMessageBox(QMessageBox.Warning, t("Open Project Folder"),
                t("Could not open the projects folder:\n\n{error}", error=exc), QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()

    def _on_clean_video_data(self):
        from PySide6.QtWidgets import QMessageBox

        confirm = QMessageBox(QMessageBox.Warning, t("Clean Video Data"),
            t("Remove all generated project data and video preview caches?\n\n"
              "Source videos, downloaded models, Piper voices, CUDA files, and application resources will not be touched."),
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
            message = QMessageBox(QMessageBox.Warning, t("Clean Video Data"),
                t("Some data could not be removed:\n\n{detail}", detail=detail), QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()
        else:
            message = QMessageBox(QMessageBox.Information, t("Clean Video Data"),
                t("Generated project data and video caches were cleared."), QMessageBox.Ok, self)
            message.setStyleSheet(MSG_STYLE)
            message.exec()

    def _on_about(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices, QPixmap
        from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QTextBrowser, QVBoxLayout, QHBoxLayout

        dialog = QDialog(self)
        dialog.setWindowTitle(t("About CapCap"))
        dialog.setMinimumSize(650, 650)
        dialog.setStyleSheet("QDialog { background: #0a101e; color: #d7e3f4; }")
        layout = QVBoxLayout(dialog)
        title = QLabel(t("CapCap V8 — Tool Information"), dialog)
        title.setStyleSheet("font-size: 18px; font-weight: 800; color: #ffffff;")
        layout.addWidget(title)

        browser = QTextBrowser(dialog)
        browser.setOpenExternalLinks(True)
        browser.setStyleSheet(
            "QTextBrowser { background: #0f1928; color: #d7e3f4; border: 1px solid #1e3045; "
            "border-radius: 8px; padding: 10px; }"
        )
        browser.setHtml(f"""
        <h3 style='color:#8ad7ff;'>{t("Description")}</h3>
        <p>{t("CapCap is a Windows application that supports both CPU and GPU processing.")}</p>
        <p>{t("GPU mode provides the best overall experience and performance. GPU acceleration currently supports NVIDIA GPUs.")}</p>
        <p>{t("If CUDA is not detected correctly, first update your NVIDIA GPU driver. If needed, install CUDA 12.8 from:")}<br>
        <a href='https://developer.nvidia.com/cuda-12-8-0-download-archive'>CUDA 12.8 Download Archive</a></p>

        <h3 style='color:#8ad7ff;'>{t("Tutorial / Resource Setup")}</h3>
        <p>{t("Download the resource, then place it in the matching CapCap folder:")}</p>
        <table cellspacing='6'>
        <tr><td><b>{t("Whisper models")}</b></td><td><code>CapCap\\models\\faster_whisper</code></td></tr>
        <tr><td><b>{t("CUDA / cuDNN runtime")}</b></td><td><code>CapCap\\bin\\cuda12_fw</code></td></tr>
        <tr><td><b>{t("SenseVoice")}</b></td><td>{t("Bundled by default in")} <code>CapCap\\models\\sensevoice</code></td></tr>
        <tr><td><b>{t("RapidOCR models")}</b></td><td>{t("Bundled by default; optional files use")} <code>CapCap\\rapidocr\\models</code></td></tr>
        <tr><td><b>{t("Piper voices")}</b></td><td><code>CapCap\\models\\piper</code> ({t("Vietnamese")}: shared <code>config.json</code>) {t("or")} <code>CapCap\\models\\piper-en</code> ({t("English")})</td></tr>
        <tr><td><b>{t("Speaker Detection")}</b></td><td><code>CapCap\\models\\pyannote</code></td></tr>
        </table>
        <p>{t("Resource Manager provides download links for supported optional resources. Extract downloaded archives into the folder shown above.")}</p>

        <h3 style='color:#8ad7ff;'>{t("How to Setup")}</h3>
        <p>{t("CapCap has two processing modes:")} <b>{t("CPU Mode")}</b> {t("and")} <b>{t("GPU Mode")}</b>.</p>
        <p><b>{t("CPU Mode")}:</b> {t("Ready to use immediately without additional downloads. Optional resources add more models, voices, or features.")}</p>
        <p><b>{t("GPU Mode")}:</b> {t("Requires the GPU Acceleration Pack. Download and extract it into")} <code>CapCap\\bin</code>. {t("Whisper Medium is optional but recommended for better GPU transcription quality.")}</p>
        <p>{t("Other resources are optional enhancements. CapCap works without them unless you select a feature that needs one.")}</p>

        <h3 style='color:#8ad7ff;'>{t("How to Use")}</h3>
        <p><b>{t("Left side:")}</b> {t("Workflow progress, configuration, and options.")}</p>
        <p><b>{t("Right side — Top:")}</b> {t("Video Preview and action buttons on the left; the selected Timeline layer's Inspector on the right.")}</p>
        <p><b>{t("Right side — Bottom:")}</b> {t("Timeline Editor and timeline editing actions.")}</p>
        <ol>
        <li>{t("Use the setup guidance above and download any resources you need.")}</li>
        <li>{t("Open Settings and select the Subtitle Source and AI Translation provider.")}</li>
        <li>{t("In Language, select the input and output languages.")}</li>
        <li>{t("Click Generate: choose Full Pipeline to run automatically, or Step-by-Step for individual phase control.")}</li>
        </ol>

        <h3 style='color:#8ad7ff;'>{t("Developer Information")}</h3>
        <p>GitHub: <a href='https://github.com/notepower2k1/CapCap'>github.com/notepower2k1/CapCap</a></p>
        """)
        layout.addWidget(browser, 1)

        donation_row = QHBoxLayout()
        donation_row.setSpacing(18)
        donation_label = QLabel(t("Donate Vietnam\nScan to support development"), dialog)
        donation_label.setStyleSheet("color:#d7e3f4; font-weight:600;")
        qr_label = QLabel(dialog)
        qr_label.setAlignment(Qt.AlignCenter)
        qr_path = asset_path("qr.png")
        qr_pixmap = QPixmap(qr_path)
        if not qr_pixmap.isNull():
            qr_label.setPixmap(qr_pixmap.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            qr_label.setText(t("QR unavailable"))
        donation_row.addWidget(donation_label)
        donation_row.addWidget(qr_label)
        donation_row.addStretch()

        coffee_group = QHBoxLayout()
        coffee_group.setSpacing(5)
        coffee_text = QLabel(t("International Donation\nClick to Buy Me a Coffee"), dialog)
        coffee_text.setStyleSheet("color:#d7e3f4; font-weight:600;")
        coffee_group.addWidget(coffee_text)
        coffee_path = asset_path("buymeacoffee.png")
        coffee_pixmap = QPixmap(coffee_path)
        coffee_image = QLabel(dialog)
        coffee_image.setAlignment(Qt.AlignCenter)
        if not coffee_pixmap.isNull():
            coffee_image.setPixmap(coffee_pixmap.scaled(190, 100, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        else:
            coffee_image.setText(t("Buy Me a Coffee image unavailable"))
        coffee_image.setToolTip(t("Open Buy Me a Coffee"))
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
            self, t("Select Long Video to Split"), "",
            t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm);;All Files (*)")
        )
        if not path:
            return

        duration = _get_video_duration(path)
        if duration <= 0:
            mb = QMessageBox(QMessageBox.Warning, t("Invalid Video"),
                t("Could not determine video duration."),
                QMessageBox.Ok, self)
            mb.setStyleSheet(MSG_STYLE)
            mb.exec()
            return

        h = int(duration // 3600)
        m = int((duration % 3600) // 60)

        seg_minutes, ok = QInputDialog.getInt(
            self, t("Segment Duration"),
            t("Video is {hours}h {minutes}m.\nSplit into segments of how many minutes?", hours=h, minutes=m),
            120, 10, 1440, 10,
        )
        if not ok:
            return

        seg_seconds = seg_minutes * 60
        base, ext = os.path.splitext(path)
        out_pattern = f"{base}_part%03d{ext}"

        reply = QMessageBox(QMessageBox.Question, t("Confirm Split"),
            t("Split into {minutes}-minute segments using stream copy (no re-encode, fast).\n\nOutput: {output}\n\nContinue?", minutes=seg_minutes, output=out_pattern),
            QMessageBox.Yes | QMessageBox.No, self)
        reply.setStyleSheet(MSG_STYLE)
        if reply.exec() != QMessageBox.Yes:
            return

        progress = QProgressDialog(t("Splitting video..."), None, 0, 0, self)
        progress.setWindowTitle(t("Split Video"))
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

        mb = QMessageBox(QMessageBox.Information, t("Done"),
            t("Video split into {minutes}-minute segments.\nSaved alongside the original file.", minutes=seg_minutes),
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
        self._last_has_gpu = bool(has_gpu)
        self._last_gpu_name = str(gpu_name or "")
        self._last_cuda_ready = bool(cuda_ready)
        if has_gpu:
            if cuda_ready:
                self._gpu_label.setText(t("GPU: {gpu_name}  ✓ CUDA ready", gpu_name=gpu_name))
                self._gpu_label.setStyleSheet("font-size: 11px; color: #4ecdc4;")
            else:
                self._gpu_label.setText(t("GPU: {gpu_name}  ✗ Need GPU Acceleration Pack", gpu_name=gpu_name))
                self._gpu_label.setStyleSheet("font-size: 11px; color: #ffa500;")
        else:
            self._gpu_label.setText(t("CPU only"))
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


def show_launcher(settings_or_none, project_loader=None):
    """Show launcher, return selected video path or empty string."""
    w = LauncherWindow()
    result = w.exec()
    selected_video = str(getattr(w, "selected_video", "") or "")
    worker = getattr(w, "_cache_worker", None)
    if worker is not None and worker.isRunning():
        try:
            worker.requestInterruption()
            worker.wait(2000)
        except Exception:
            pass
    try:
        w.close()
        w.deleteLater()
    except Exception:
        pass
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    QApplication.processEvents()
    if result == QDialog.Accepted:
        return selected_video
    return ""
