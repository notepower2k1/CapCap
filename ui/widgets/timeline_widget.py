from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QFrame, QGraphicsScene, QGraphicsView


class TimelineWidget(QGraphicsView):
    """CapCut-style timeline for subtitle preview and seeking."""

    seekRequested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(130)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("background-color: #0d0d0d; border-top: 1px solid #1a1a1a;")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.Antialiasing)

        self.horizontalScrollBar().setStyleSheet(
            """
            QScrollBar:horizontal {
                border: none;
                background: #111;
                height: 10px;
                margin: 0px;
            }
            QScrollBar::handle:horizontal {
                background: #333;
                min-width: 30px;
                border-radius: 5px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #444;
            }
            """
        )

        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.pixels_per_second = 100
        self.duration = 0
        self.segments = []
        self.playhead = None
        self.is_moving_playhead = False
        self._playing = False

    def set_playing(self, playing):
        self._playing = playing

    def set_duration(self, ms):
        self.duration = ms
        self.refresh()

    def set_segments(self, segments):
        self.segments = segments
        self.refresh()

    def refresh(self):
        self._scene.clear()
        width = (self.duration / 1000.0) * self.pixels_per_second
        width = max(width, self.width()) + 200
        self._scene.setSceneRect(0, 0, width, 110)

        ruler_pen = QPen(QColor(80, 80, 80), 1)
        font = QFont("Segoe UI", 7)
        for sec in range(0, int(self.duration / 1000) + 5):
            x_pos = sec * self.pixels_per_second
            is_major = sec % 5 == 0
            height = 14 if is_major else 6
            self._scene.addLine(x_pos, 0, x_pos, height, ruler_pen)
            if is_major:
                mins, secs = divmod(sec, 60)
                label = self._scene.addText(f"{mins:02d}:{secs:02d}", font)
                label.setDefaultTextColor(QColor(150, 150, 150))
                label.setPos(x_pos + 2, -2)

        row_y = 35
        row_h = 35
        for seg in self.segments:
            start_x = seg["start"] * self.pixels_per_second
            end_x = seg["end"] * self.pixels_per_second
            seg_w = max(2, end_x - start_x)

            rect = self._scene.addRect(0, 0, seg_w, row_h, QPen(QColor(60, 60, 60), 1), QColor(41, 121, 255, 120))
            rect.setPos(start_x, row_y)
            rect.setToolTip(seg["text"])

            clean_txt = seg["text"].replace("\n", " ").strip()
            if len(clean_txt) > 25:
                clean_txt = clean_txt[:22] + "..."
            text_item = self._scene.addText(clean_txt, QFont("Segoe UI", 8))
            text_item.setDefaultTextColor(Qt.white)
            text_item.setPos(start_x + 4, row_y + 4)

        self.playhead = self._scene.addLine(0, 0, 0, 110, QPen(QColor(255, 40, 40), 2))
        if self.playhead:
            self.playhead.setZValue(1000)

    def set_position(self, ms):
        if not self.playhead:
            return
        x_pos = (ms / 1000.0) * self.pixels_per_second
        self.playhead.setLine(x_pos, 0, x_pos, 110)

        if self._playing:
            view_rect = self.viewport().rect()
            scene_rect = self.mapToScene(view_rect).boundingRect()
            if x_pos > scene_rect.right() - 100 or x_pos < scene_rect.left():
                self.centerOn(x_pos, 55)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_moving_playhead = True
            self.handle_seek(event.position().toPoint())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.is_moving_playhead:
            self.handle_seek(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.is_moving_playhead = False
        super().mouseReleaseEvent(event)

    def handle_seek(self, pos):
        scene_pos = self.mapToScene(pos)
        ms = int((scene_pos.x() / self.pixels_per_second) * 1000)
        ms = max(0, min(ms, self.duration))
        self.seekRequested.emit(ms)
