from PySide6.QtCore import QRectF, Qt
from PySide6.QtWidgets import QWidget


class _NullSubtitleOverlay:
    W = 640
    H = 96

    def __init__(self):
        self.alignment = "Bottom Center"
        self.x_offset = 0
        self.bottom_offset = 30

    def hide(self):
        return None

    def set_style(self, **kwargs):
        if "font_size" in kwargs and kwargs["font_size"]:
            self.H = max(96, int(kwargs["font_size"] * 4))

    def set_alignment(self, alignment: str):
        self.alignment = alignment or "Bottom Center"

    def set_positioning(self, *, x_offset=None, bottom_offset=None):
        if x_offset is not None:
            self.x_offset = x_offset
        if bottom_offset is not None:
            self.bottom_offset = bottom_offset

    def set_layout_width(self, width: int):
        self.W = max(160, int(width))


class MpvVideoView(QWidget):
    """A native widget host for libmpv playback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_NativeWindow, True)
        self.setAutoFillBackground(True)
        self.setStyleSheet("background-color: black; border-radius: 10px;")
        self.setMinimumSize(320, 180)
        self.video_source_width = 0
        self.video_source_height = 0
        self.subtitle_item = _NullSubtitleOverlay()

    def set_video_dimensions(self, width: int, height: int):
        self.video_source_width = max(0, int(width or 0))
        self.video_source_height = max(0, int(height or 0))

    def get_video_content_rect(self) -> QRectF:
        return QRectF(0, 0, float(self.width()), float(self.height()))

    def reposition_subtitle(self):
        return None
