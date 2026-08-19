from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QGraphicsItem


class SubtitleOverlayItem(QGraphicsItem):
    """A draggable graphics item to represent the subtitle overlay on VideoView."""
    W, H = 640, 96
    LINE_HEIGHT_FACTOR = 1.35

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)
        self.setZValue(10)
        self.current_text = ""
        self.current_lines: list[str] = []
        self.font_name = "Segoe UI"
        self.font_size = 20
        self.font_color = QColor(255, 255, 255)
        self.outline_width = 2
        self.outline_color = QColor(0, 0, 0, 220)
        self.alignment = "Bottom Center"
        self.background_box = False
        self.background_color = QColor(0, 0, 0, 170)
        self.single_line = False
        self.bold = True
        self.shadow_color = QColor(0, 0, 0, 180)
        self.shadow_depth = 0.0
        self.x_offset = 0
        self.bottom_offset = 30
        self.custom_position_enabled = False
        self.custom_x_percent = 50
        self.text_rendering = True
        self._editable = False
        self._suppressed = False

    def set_editable(self, editable: bool, *args, **kwargs):
        self._editable = bool(editable)

    def set_suppressed(self, suppressed: bool, *args, **kwargs):
        self._suppressed = bool(suppressed)

    def attach_to_view(self, view, *args, **kwargs):
        pass

    def is_top_level_overlay(self, *args, **kwargs) -> bool:
        return False

    def sync_to_view(self, *args, **kwargs):
        pass

    def set_effects(self, *args, **kwargs):
        pass

    def set_text_rendering(self, enabled: bool):
        enabled = bool(enabled)
        if getattr(self, "text_rendering", True) != enabled:
            self.text_rendering = enabled
            self.update()

    def set_text(self, text):
        new_lines = [line for line in str(text or "").splitlines() if line] or ([] if not text else [str(text)])
        if self.current_lines != new_lines or self.current_text != text:
            self.current_text = text
            self.current_lines = new_lines
            self._update_height()
            self.update()

    def set_lines(self, lines):
        """Set multiple subtitle lines (e.g. when two segments overlap).
        Each line is drawn on its own row, stacked vertically.
        """
        cleaned = [str(line or "").strip() for line in (lines or []) if str(line or "").strip()]
        joined = "\n".join(cleaned)
        if self.current_lines != cleaned or self.current_text != joined:
            self.current_lines = cleaned
            self.current_text = joined
            self._update_height()
            self.update()

    def _update_height(self):
        line_count = max(1, len(self.current_lines))
        line_height = max(24, int(self.font_size * self.LINE_HEIGHT_FACTOR))
        new_h = max(96, int(self.font_size * 4) + max(0, line_count - 1) * line_height)
        if new_h != self.H:
            self.prepareGeometryChange()
            self.H = new_h

    def set_style(
        self,
        font_name=None,
        font_size=None,
        font_color=None,
        outline_width=None,
        outline_color=None,
        background_box=None,
        background_color=None,
        single_line=None,
        bold=None,
        shadow_color=None,
        shadow_depth=None,
        *args,
        **kwargs,
    ):
        changed = False
        if font_name and font_name != self.font_name:
            self.font_name = font_name
            changed = True
        if font_size and font_size != self.font_size:
            self.font_size = font_size
            self.prepareGeometryChange()
            self.H = max(96, int(font_size * 4) + max(0, len(self.current_lines) - 1) * int(font_size * self.LINE_HEIGHT_FACTOR))
            changed = True
        if font_color and font_color != self.font_color:
            self.font_color = font_color
            changed = True
        if outline_width is not None and float(outline_width) != self.outline_width:
            self.outline_width = max(0.0, float(outline_width))
            changed = True
        if outline_color is not None and outline_color != self.outline_color:
            self.outline_color = outline_color
            changed = True
        if bold is not None and bool(bold) != self.bold:
            self.bold = bool(bold)
            changed = True
        if shadow_color is not None:
            self.shadow_color = QColor(shadow_color)
            changed = True
        if shadow_depth is not None and float(shadow_depth) != self.shadow_depth:
            self.shadow_depth = max(0.0, float(shadow_depth))
            changed = True
        if background_box is not None and bool(background_box) != self.background_box:
            self.background_box = bool(background_box)
            changed = True
        if background_color is not None and background_color != self.background_color:
            self.background_color = background_color
            changed = True
        if single_line is not None and bool(single_line) != self.single_line:
            self.single_line = bool(single_line)
            changed = True
        if changed:
            self.update()


    def set_alignment(self, alignment: str):
        self.alignment = alignment or "Bottom Center"
        self.update()

    def set_positioning(self, *, x_offset=None, bottom_offset=None, custom_position_enabled=None, custom_x_percent=None, custom_y_percent=None):
        if x_offset is not None:
            self.x_offset = x_offset
        if bottom_offset is not None:
            self.bottom_offset = bottom_offset
        if custom_position_enabled is not None:
            self.custom_position_enabled = bool(custom_position_enabled)
        if custom_x_percent is not None:
            self.custom_x_percent = int(custom_x_percent)
        if custom_y_percent is not None:
            self.custom_y_percent = int(custom_y_percent)
        self.update()

    def set_layout_width(self, width: int):
        width = max(160, int(width))
        if width != self.W:
            self.W = width
            self.update()

    def boundingRect(self):
        return QRectF(0, 0, self.W, self.H)

    def paint(self, painter, option, widget):
        if not getattr(self, "text_rendering", True):
            return
        if not self.current_text and not self.current_lines and not self.isVisible():
            return
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.boundingRect()


        if not self.current_lines:
            return

        painter.setPen(self.font_color)
        font = QFont(self.font_name)
        font.setPixelSize(max(1, int(self.font_size)))
        font.setBold(True)
        painter.setFont(font)
        line_height = max(24, int(self.font_size * self.LINE_HEIGHT_FACTOR))
        single_line_flags = Qt.AlignCenter
        wrap_flags = Qt.AlignCenter | Qt.TextWordWrap

        line_rects: list[QRectF] = []
        if len(self.current_lines) == 1:
            line_rects = [rect]
        else:
            total_h = line_height * len(self.current_lines)
            y0 = max(0.0, (rect.height() - total_h) / 2.0)
            for i in range(len(self.current_lines)):
                line_rects.append(QRectF(rect.x(), rect.y() + y0 + i * line_height, rect.width(), line_height))

        if self.background_box:
            painter.setPen(Qt.NoPen)
            painter.setBrush(self.background_color)
            metrics = painter.fontMetrics()
            box_pad_x = max(2, int(round(max(2.0, self.outline_width))))
            box_pad_y = max(1, int(round(max(1.0, self.outline_width * 0.5))))
            max_width = max((metrics.boundingRect(line).width() for line in self.current_lines), default=0)
            if max_width and line_rects:
                block_top = line_rects[0].top()
                block_bottom = line_rects[-1].bottom()
                text_rect = QRect(
                    int(round(rect.center().x() - max_width / 2.0)),
                    int(round(block_top)),
                    max_width,
                    max(1, int(round(block_bottom - block_top))),
                ).adjusted(-box_pad_x, -box_pad_y, box_pad_x, box_pad_y)
                painter.drawRect(QRectF(text_rect))

        if self.outline_width > 0:
            w = max(1.0, float(self.outline_width))
            outline_offsets = [
                (-w, 0), (w, 0), (0, -w), (0, w),
                (-w * 0.7, -w * 0.7), (w * 0.7, -w * 0.7),
                (-w * 0.7, w * 0.7), (w * 0.7, w * 0.7),
            ]
            painter.setPen(self.outline_color)
            for line_text, line_rect in zip(self.current_lines, line_rects):
                for dx, dy in outline_offsets:
                    painter.drawText(line_rect.translated(dx, dy), int(wrap_flags), line_text)

        painter.setPen(self.font_color)
        for line_text, line_rect in zip(self.current_lines, line_rects):
            painter.drawText(line_rect, int(wrap_flags), line_text)
