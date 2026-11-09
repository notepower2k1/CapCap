"""Spin boxes with reliable mouse handling for the Windows desktop UI.

Qt's native spin-button hit testing can become asymmetric when a global
stylesheet adds padding/borders and Windows applies a high-DPI theme.  On
some Windows 11 configurations the lower half of the button still decrements
normally while clicks on the upper half are treated as clicks in the line
edit.  The small mixin below keeps the native appearance and keyboard/wheel
behavior, but handles clicks in the right-hand arrow area explicitly.
"""

from PySide6.QtCore import QEvent, QTimer, QRect, Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QSpinBox,
    QStyle,
    QStyleOptionSpinBox,
)


class _ReliableSpinButtonsMixin:
    """Make both spin-button halves clickable across Qt/Windows styles."""

    def _init_reliable_spin_buttons(self):
        self._manual_arrow_direction = ""
        # On the Windows 11 style the internal line edit can cover part of
        # the painted arrow strip.  Observe it as well as the spin box itself
        # so a click on the upper button cannot be consumed as text focus.
        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.installEventFilter(self)
        self._arrow_repeat_delay = QTimer(self)
        self._arrow_repeat_delay.setSingleShot(True)
        self._arrow_repeat_delay.setInterval(400)
        self._arrow_repeat_delay.timeout.connect(self._start_arrow_repeat)
        self._arrow_repeat_timer = QTimer(self)
        self._arrow_repeat_timer.setInterval(90)
        self._arrow_repeat_timer.timeout.connect(self._repeat_arrow_step)

    def _arrow_direction_at(self, point):
        option = QStyleOptionSpinBox()
        option.initFrom(self)
        option.rect = self.rect()
        style = self.style()
        up_rect = style.subControlRect(
            QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxUp, self
        )
        down_rect = style.subControlRect(
            QStyle.CC_SpinBox, option, QStyle.SC_SpinBoxDown, self
        )
        if up_rect.isValid() and up_rect.contains(point):
            return "up"
        if down_rect.isValid() and down_rect.contains(point):
            return "down"

        # A few native/high-DPI styles report empty or undersized subcontrol
        # rectangles while still painting the arrows.  Reserve a generous,
        # predictable hit strip as a fallback so the upper arrow cannot be
        # lost behind the line-edit padding.
        # Qt does not expose a portable ``PM_SpinBoxButtonWidth`` metric in
        # all PySide6 releases, so use a DPI-independent minimum hit target.
        # The actual native subcontrol rectangles above are preferred when
        # available.
        button_width = 48
        if self.layoutDirection() == Qt.RightToLeft:
            left = 0
        else:
            left = max(0, self.width() - button_width)
        hit_rect = QRect(left, 0, min(button_width, self.width()), self.height())
        if not hit_rect.contains(point):
            return ""
        return "up" if point.y() < max(1, self.height() // 2) else "down"

    def _step_manual_arrow(self):
        if self._manual_arrow_direction == "up":
            self.stepUp()
        elif self._manual_arrow_direction == "down":
            self.stepDown()

    def _start_arrow_repeat(self):
        if self._manual_arrow_direction:
            self._arrow_repeat_timer.start()

    def _repeat_arrow_step(self):
        if self._manual_arrow_direction:
            self._step_manual_arrow()

    def _stop_arrow_repeat(self):
        self._arrow_repeat_delay.stop()
        self._arrow_repeat_timer.stop()
        self._manual_arrow_direction = ""

    def _begin_manual_arrow(self, direction):
        self._manual_arrow_direction = direction
        self.setFocus(Qt.MouseFocusReason)
        self._step_manual_arrow()
        self._arrow_repeat_delay.start()

    def eventFilter(self, watched, event):
        line_edit = self.lineEdit()
        if line_edit is not None and watched is line_edit:
            event_type = event.type()
            if event_type == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                point = line_edit.mapTo(self, event.position().toPoint())
                direction = self._arrow_direction_at(point)
                if direction:
                    self._begin_manual_arrow(direction)
                    event.accept()
                    return True
            elif event_type == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self._manual_arrow_direction:
                    self._stop_arrow_repeat()
                    event.accept()
                    return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            direction = self._arrow_direction_at(event.position().toPoint())
            if direction:
                self._begin_manual_arrow(direction)
                event.accept()
                return
        self._stop_arrow_repeat()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._manual_arrow_direction and event.button() == Qt.LeftButton:
            self._stop_arrow_repeat()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        self._stop_arrow_repeat()
        super().focusOutEvent(event)


class ReliableSpinBox(_ReliableSpinButtonsMixin, QSpinBox):
    """QSpinBox with a dependable increment/decrement hit target."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_reliable_spin_buttons()


class ReliableDoubleSpinBox(_ReliableSpinButtonsMixin, QDoubleSpinBox):
    """QDoubleSpinBox with a dependable increment/decrement hit target."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_reliable_spin_buttons()


class NoWheelSpinBox(ReliableSpinBox):
    """QSpinBox that ignores mouse wheel events to prevent accidental changes while scrolling."""

    def wheelEvent(self, event):
        event.ignore()


__all__ = ["ReliableSpinBox", "ReliableDoubleSpinBox", "NoWheelSpinBox"]
