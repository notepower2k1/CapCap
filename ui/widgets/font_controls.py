"""Shared percentage input for subtitle and ordinary text sizes."""
from widgets.spin_boxes import ReliableDoubleSpinBox
from i18n import t


def font_scale_input(parent=None):
    control = ReliableDoubleSpinBox(parent)
    control.setDecimals(2)
    control.setKeyboardTracking(False)
    control.setRange(100 / 60, 100000 / 60)
    control.setSingleStep(5)
    control.setSuffix("%")
    control.setValue(100)
    control.setToolTip(t("100% = 60 px. Font size is rounded to whole pixels; the percentage shows the actual stored size."))
    return control
