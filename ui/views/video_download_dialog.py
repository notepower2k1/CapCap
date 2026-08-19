from __future__ import annotations

import os
import urllib.request
from typing import Optional

from PySide6.QtCore import QThread, Signal, Qt, QTimer
from PySide6.QtGui import QPixmap, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QFrame,
    QMessageBox,
    QWidget,
    QFileDialog,
    QComboBox,
)

from services.video_download_service import VideoDownloadService
from runtime_paths import temp_path, bin_path


class _DownloadWorker(QThread):
    sig_progress = Signal(int, str, str)  # percent, speed_str, message
    sig_finished = Signal(str, dict)       # output_path, info
    sig_error = Signal(str)

    def __init__(
        self,
        url: str,
        output_dir: str = "",
        cookie_file: str = "",
        browser_cookies: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.url = url
        self.output_dir = output_dir
        self.cookie_file = cookie_file
        self.browser_cookies = browser_cookies

    def run(self):
        try:
            service = VideoDownloadService(self.output_dir)

            def _on_progress(pct: int, spd: str, msg: str):
                self.sig_progress.emit(pct, spd, msg)

            video_path, info = service.download_video(
                self.url,
                output_dir=self.output_dir,
                cookie_file=self.cookie_file,
                browser_cookies=self.browser_cookies,
                progress_callback=_on_progress,
            )
            self.sig_finished.emit(video_path, info)
        except Exception as exc:
            self.sig_error.emit(str(exc))


class _InfoFetchWorker(QThread):
    sig_info_ready = Signal(dict)
    sig_error = Signal(str)

    def __init__(
        self,
        url: str,
        cookie_file: str = "",
        browser_cookies: str = "",
        parent=None,
    ):
        super().__init__(parent)
        self.url = url
        self.cookie_file = cookie_file
        self.browser_cookies = browser_cookies

    def run(self):
        try:
            service = VideoDownloadService()
            info = service.get_video_info(
                self.url,
                cookie_file=self.cookie_file,
                browser_cookies=self.browser_cookies,
            )
            self.sig_info_ready.emit(info)
        except Exception as exc:
            self.sig_error.emit(str(exc))


class VideoDownloadDialog(QDialog):
    """
    Dialog to download videos directly from URL
    (Douyin, TikTok, YouTube, Bilibili, Facebook, etc.) and create a project.
    """

    def __init__(self, parent: Optional[QWidget] = None, output_dir: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Download Video from Link")
        self.setMinimumWidth(580)
        self.setMinimumHeight(420)
        self.setStyleSheet(
            "background-color: #0c1524; color: #e2e8f0; font-family: 'Segoe UI', sans-serif;"
        )

        self.output_dir = output_dir or temp_path("downloads")
        self.downloaded_video_path: str = ""
        self._download_worker: Optional[_DownloadWorker] = None
        self._info_worker: Optional[_InfoFetchWorker] = None
        self._current_info: dict = {}

        self._build_ui()
        self._check_default_cookies()
        self._check_clipboard()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(24, 24, 24, 24)
        main_layout.setSpacing(14)

        # Header
        header_layout = QVBoxLayout()
        header_layout.setSpacing(4)
        title_label = QLabel("Download Video from Link")
        title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #f8fafc;")
        subtitle_label = QLabel("Supports Douyin, TikTok, Bilibili, YouTube, Kuaishou, Facebook Reels, and more.")
        subtitle_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)
        main_layout.addLayout(header_layout)

        # URL Input Row
        input_row = QHBoxLayout()
        input_row.setSpacing(8)

        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Paste video link or share text here...")
        self.url_input.setMinimumHeight(40)
        self.url_input.setStyleSheet("""
            QLineEdit {
                background-color: #142437;
                border: 1px solid #2e4b68;
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
                color: #f1f5f9;
            }
            QLineEdit:focus {
                border: 1px solid #38bdf8;
                background-color: #182c44;
            }
        """)
        self.url_input.textChanged.connect(self._on_url_changed)
        input_row.addWidget(self.url_input, 1)

        self.paste_btn = QPushButton("Paste")
        self.paste_btn.setMinimumHeight(40)
        self.paste_btn.setMinimumWidth(80)
        self.paste_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e324a;
                color: #8ad7ff;
                border: 1px solid #34506f;
                border-radius: 8px;
                font-weight: 600;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #29405d; }
        """)
        self.paste_btn.clicked.connect(self._paste_from_clipboard)
        input_row.addWidget(self.paste_btn)
        main_layout.addLayout(input_row)

        # Cookie Settings Row (For Douyin / Bilibili / Private Videos)
        cookie_frame = QFrame()
        cookie_frame.setStyleSheet("""
            QFrame {
                background-color: #101c2e;
                border: 1px solid #1e334e;
                border-radius: 8px;
                padding: 6px 10px;
            }
        """)
        cookie_layout = QHBoxLayout(cookie_frame)
        cookie_layout.setContentsMargins(4, 4, 4, 4)
        cookie_layout.setSpacing(8)

        cookie_lbl = QLabel("Cookie (Optional):")
        cookie_lbl.setStyleSheet("font-size: 11px; color: #94a3b8; font-weight: 600;")
        cookie_layout.addWidget(cookie_lbl)

        self.cookie_path_edit = QLineEdit()
        self.cookie_path_edit.setPlaceholderText("Path to cookies.txt (for Douyin / Bilibili)...")
        self.cookie_path_edit.setStyleSheet("""
            QLineEdit {
                background-color: #142437;
                border: 1px solid #253d5a;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 11px;
                color: #cbd5e1;
            }
        """)
        cookie_layout.addWidget(self.cookie_path_edit, 1)

        self.browse_cookie_btn = QPushButton("Browse...")
        self.browse_cookie_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e324a;
                color: #8ad7ff;
                border: 1px solid #34506f;
                border-radius: 6px;
                font-size: 11px;
                padding: 4px 10px;
            }
            QPushButton:hover { background-color: #29405d; }
        """)
        self.browse_cookie_btn.clicked.connect(self._browse_cookie_file)
        cookie_layout.addWidget(self.browse_cookie_btn)

        main_layout.addWidget(cookie_frame)

        # Info Preview Card
        self.info_card = QFrame()
        self.info_card.setStyleSheet("""
            QFrame {
                background-color: #142437;
                border: 1px solid #253d5a;
                border-radius: 10px;
                padding: 10px;
            }
        """)
        info_layout = QHBoxLayout(self.info_card)
        info_layout.setContentsMargins(6, 6, 6, 6)
        info_layout.setSpacing(12)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(110, 70)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.thumb_label.setStyleSheet(
            "background-color: #0d1a29; border-radius: 6px; color: #64748b; font-size: 11px;"
        )
        self.thumb_label.setText("No Preview")
        info_layout.addWidget(self.thumb_label)

        meta_layout = QVBoxLayout()
        meta_layout.setSpacing(4)

        self.title_text = QLabel("Video Title")
        self.title_text.setWordWrap(True)
        self.title_text.setStyleSheet("font-size: 13px; font-weight: 600; color: #f1f5f9;")
        meta_layout.addWidget(self.title_text)

        badges_row = QHBoxLayout()
        badges_row.setSpacing(8)

        self.duration_badge = QLabel("Duration: --:--")
        self.duration_badge.setStyleSheet(
            "font-size: 11px; color: #38bdf8; background-color: #0f2438; border-radius: 4px; padding: 2px 6px;"
        )
        badges_row.addWidget(self.duration_badge)

        self.author_badge = QLabel("Author: --")
        self.author_badge.setStyleSheet(
            "font-size: 11px; color: #94a3b8; background-color: #0f2438; border-radius: 4px; padding: 2px 6px;"
        )
        badges_row.addWidget(self.author_badge)
        badges_row.addStretch()
        meta_layout.addLayout(badges_row)

        info_layout.addLayout(meta_layout, 1)
        main_layout.addWidget(self.info_card)
        self.info_card.hide()

        # Progress Section
        self.progress_section = QVBoxLayout()
        self.progress_section.setSpacing(6)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-size: 12px; color: #38bdf8; font-weight: 500;")
        self.progress_section.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(18)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #142437;
                border: 1px solid #2e4b68;
                border-radius: 9px;
                text-align: center;
                color: #f8fafc;
                font-size: 10px;
                font-weight: 700;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdf8, stop:1 #4ecdc4);
                border-radius: 8px;
            }
        """)
        self.progress_section.addWidget(self.progress_bar)
        main_layout.addLayout(self.progress_section)
        self.progress_bar.hide()
        self.status_label.hide()

        main_layout.addStretch()

        # Action Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        btn_row.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumHeight(40)
        self.cancel_btn.setMinimumWidth(100)
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #94a3b8;
                border: 1px solid #334155;
                border-radius: 8px;
                font-weight: 600;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #334155; color: #f8fafc; }
        """)
        self.cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(self.cancel_btn)

        self.download_btn = QPushButton("Download and Open")
        self.download_btn.setMinimumHeight(40)
        self.download_btn.setMinimumWidth(180)
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #4ecdc4;
                color: #0a101e;
                font-weight: 700;
                font-size: 13px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover { background-color: #6ee7d6; }
            QPushButton:disabled { background-color: #1e293b; color: #475569; }
        """)
        self.download_btn.clicked.connect(self._start_download)
        self.download_btn.setEnabled(False)
        btn_row.addWidget(self.download_btn)

        main_layout.addLayout(btn_row)

    def _check_default_cookies(self):
        """Auto-detect if a cookies.txt file exists in standard locations."""
        candidates = [
            bin_path("cookies.txt"),
            bin_path("douyin_cookies.txt"),
            os.path.abspath("cookies.txt"),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.path.getsize(c) > 0:
                self.cookie_path_edit.setText(c)
                break

    def _browse_cookie_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select cookies.txt file",
            self.cookie_path_edit.text() or "",
            "Cookie Files (*.txt);;All Files (*)",
        )
        if path:
            self.cookie_path_edit.setText(path)

    def _check_clipboard(self):
        """Auto-populate URL from clipboard if it contains http(s) link."""
        try:
            clipboard = QGuiApplication.clipboard()
            text = clipboard.text().strip()
            url = VideoDownloadService.extract_url(text)
            if url:
                self.url_input.setText(text)
        except Exception:
            pass

    def _paste_from_clipboard(self):
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text().strip()
        if text:
            self.url_input.setText(text)

    def _on_url_changed(self, text: str):
        url = VideoDownloadService.extract_url(text)
        self.download_btn.setEnabled(bool(url))
        if url and not self._current_info:
            QTimer.singleShot(400, self._fetch_info_preview)

    def _fetch_info_preview(self):
        url = VideoDownloadService.extract_url(self.url_input.text())
        if not url or (self._info_worker and self._info_worker.isRunning()):
            return

        cookie_file = self.cookie_path_edit.text().strip()
        self._info_worker = _InfoFetchWorker(url, cookie_file=cookie_file, parent=self)
        self._info_worker.sig_info_ready.connect(self._on_info_ready)
        self._info_worker.start()

    def _on_info_ready(self, info: dict):
        self._current_info = info
        title = info.get("title", "")
        duration_s = info.get("duration", 0) or 0
        mins = int(duration_s // 60)
        secs = int(duration_s % 60)
        author = info.get("uploader", "") or "Unknown"
        thumb_url = info.get("thumbnail", "")

        self.title_text.setText(title)
        self.duration_badge.setText(f"Duration: {mins:02d}:{secs:02d}" if duration_s else "Duration: Short/Live")
        self.author_badge.setText(f"Author: {author}")
        self.info_card.show()

        if thumb_url:
            self._load_thumbnail_async(thumb_url)

    def _load_thumbnail_async(self, url: str):
        def _fetch():
            try:
                import tempfile
                tmp = os.path.join(tempfile.gettempdir(), "capcap_dl_thumb.jpg")
                urllib.request.urlretrieve(url, tmp)
                if os.path.exists(tmp):
                    pixmap = QPixmap(tmp).scaled(
                        110, 70, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
                    )
                    self.thumb_label.setPixmap(pixmap)
            except Exception:
                pass

        import threading
        threading.Thread(target=_fetch, daemon=True).start()

    def _start_download(self):
        url = VideoDownloadService.extract_url(self.url_input.text())
        if not url:
            QMessageBox.warning(self, "Invalid URL", "Please enter a valid video URL.")
            return

        self.download_btn.setEnabled(False)
        self.url_input.setEnabled(False)
        self.paste_btn.setEnabled(False)
        self.browse_cookie_btn.setEnabled(False)
        self.progress_bar.show()
        self.progress_bar.setValue(5)
        self.status_label.show()
        self.status_label.setText("Connecting to video server...")

        cookie_file = self.cookie_path_edit.text().strip()

        self._download_worker = _DownloadWorker(
            url,
            output_dir=self.output_dir,
            cookie_file=cookie_file,
            parent=self,
        )
        self._download_worker.sig_progress.connect(self._on_download_progress)
        self._download_worker.sig_finished.connect(self._on_download_finished)
        self._download_worker.sig_error.connect(self._on_download_error)
        self._download_worker.start()

    def _on_download_progress(self, percent: int, speed: str, msg: str):
        self.progress_bar.setValue(percent)
        self.status_label.setText(msg)

    def _on_download_finished(self, video_path: str, info: dict):
        self.downloaded_video_path = video_path
        self.progress_bar.setValue(100)
        self.status_label.setText("Download complete! Opening project...")
        QTimer.singleShot(600, self.accept)

    def _on_download_error(self, err_msg: str):
        self.download_btn.setEnabled(True)
        self.url_input.setEnabled(True)
        self.paste_btn.setEnabled(True)
        self.browse_cookie_btn.setEnabled(True)
        self.status_label.setText(f"Download failed: {err_msg[:60]}...")

        help_text = ""
        if "cookies" in err_msg.lower() or "fresh cookies" in err_msg.lower() or "douyin" in err_msg.lower():
            help_text = (
                "\n\nTip for Douyin / Bilibili:\n"
                "This platform requires cookies. You can export cookies from your browser using a browser extension "
                "(such as 'Get cookies.txt LOCALLY') and select the exported cookies.txt file in the Cookie section, "
                "or place it at bin/cookies.txt."
            )

        QMessageBox.critical(
            self,
            "Download Error",
            f"Failed to download video:\n\n{err_msg}{help_text}",
        )
