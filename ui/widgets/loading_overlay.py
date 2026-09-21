"""In-window loading overlay for MainWindow.

Prevents user interaction and masks initial layout/media settling without
creating a separate top-level OS window/HWND, thus avoiding D3D11 swapchain
conflicts with MPV.
"""
import os
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QProgressBar, QFrame, QGraphicsOpacityEffect
)
from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QEvent

try:
    from i18n import t
except ImportError:
    from ui.i18n import t


class MainWindowLoadingOverlay(QWidget):
    """Semi-opaque or solid overlay inside MainWindow that displays a smooth loading indicator."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("mainWindowLoadingOverlay")
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet("""
            #mainWindowLoadingOverlay {
                background-color: #0c1017;
            }
            #overlayCard {
                background-color: #141b27;
                border: 1px solid #23334d;
                border-radius: 12px;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addStretch(1)

        self.card = QFrame(self)
        self.card.setObjectName("overlayCard")
        self.card.setFixedWidth(460)

        card_layout = QVBoxLayout(self.card)
        card_layout.setSpacing(14)
        card_layout.setContentsMargins(32, 28, 32, 28)

        # Title
        self.title_label = QLabel(t("CapCap Video Translator"), self.card)
        self.title_label.setStyleSheet("font-size: 18px; font-weight: 700; color: #ffffff;")
        self.title_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.title_label)

        # Video name / subtitle
        self.subtitle_label = QLabel("", self.card)
        self.subtitle_label.setStyleSheet("font-size: 13px; color: #7dd3fc; font-weight: 500;")
        self.subtitle_label.setAlignment(Qt.AlignCenter)
        self.subtitle_label.setWordWrap(True)
        card_layout.addWidget(self.subtitle_label)

        # Progress bar (smooth indeterminate pulse animation)
        self.progress_bar = QProgressBar(self.card)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1a2332;
                border-radius: 3px;
                border: none;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #38bdf8, stop:1 #818cf8);
                border-radius: 3px;
            }
        """)
        card_layout.addWidget(self.progress_bar)

        # Status text
        self.status_label = QLabel(t("Loading project & initializing player..."), self.card)
        self.status_label.setStyleSheet("font-size: 12px; color: #94a3b8;")
        self.status_label.setAlignment(Qt.AlignCenter)
        card_layout.addWidget(self.status_label)

        main_layout.addWidget(self.card, 0, Qt.AlignCenter)
        main_layout.addStretch(1)

        # Fallback safety timer: guarantees overlay is dismissed even if media load hangs
        self._safety_timer = QTimer(self)
        self._safety_timer.setSingleShot(True)
        self._safety_timer.timeout.connect(lambda: self.dismiss(fade=False))

        if parent is not None:
            parent.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched == self.parent() and event.type() in (QEvent.Resize, QEvent.Show):
            if self.parent():
                self.setGeometry(self.parent().rect())
        return super().eventFilter(watched, event)

    def show_for_video(self, video_path: str = "", max_timeout_ms: int = 4000):
        """Show overlay with video name and start safety timeout."""
        name = os.path.basename(video_path) if video_path else ""
        self.subtitle_label.setText(name)
        self.subtitle_label.setVisible(bool(name))
        if self.parent():
            self.setGeometry(self.parent().rect())
        self.raise_()
        self.show()
        if max_timeout_ms > 0:
            self._safety_timer.start(max_timeout_ms)

    def set_status(self, text: str):
        self.status_label.setText(text)

    def dismiss(self, fade: bool = False):
        """Dismiss overlay smoothly and safely."""
        self._safety_timer.stop()
        if not self.isVisible():
            return
        if not fade:
            self.hide()
            return

        try:
            self._effect = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(self._effect)
            self._anim = QPropertyAnimation(self._effect, b"opacity")
            self._anim.setDuration(180)
            self._anim.setStartValue(1.0)
            self._anim.setEndValue(0.0)
            self._anim.setEasingCurve(QEasingCurve.OutQuad)
            self._anim.finished.connect(self._on_fade_finished)
            self._anim.start()
        except Exception:
            self.hide()

    def _on_fade_finished(self):
        self.hide()
        try:
            self.setGraphicsEffect(None)
        except Exception:
            pass
