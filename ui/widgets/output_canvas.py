from PySide6.QtWidgets import QInputDialog, QMessageBox
from i18n import t
from app.output_geometry import normalize_canvas


def ensure_canvas_item(combo, value):
    try:
        key = normalize_canvas(value)
    except ValueError:
        return -1
    index = combo.findData(key)
    if index < 0:
        combo.addItem(key.removeprefix("size:"), key)
        index = combo.findData(key)
    return index


def choose_custom_canvas(gui):
    combo = gui.output_ratio_combo
    text = str(combo.currentData() or "3:2").removeprefix("size:")
    while True:
        text, accepted = QInputDialog.getText(gui, t("Custom video canvas"),
            t("Ratio (3:2) or size in pixels (1080x1920). Exact size overrides Quality."), text=text)
        if not accepted:
            return
        try:
            normalize_canvas(text)
        except ValueError as exc:
            QMessageBox.warning(gui, t("Error"), t(str(exc)))
            continue
        combo.setCurrentIndex(ensure_canvas_item(combo, text))
        return
