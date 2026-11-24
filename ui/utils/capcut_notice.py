from PySide6.QtWidgets import QMessageBox

from i18n import t


_shown = False


def watch_capcut_selection(combo, *, connection=False):
    combo.activated.connect(
        lambda index: _show_notice(combo.window(), "capcut" if connection else combo.itemData(index))
    )


def _show_notice(parent, service):
    global _shown
    key = str(service or "").lower()
    if _shown or (key != "capcut" and not key.startswith("capcut:")):
        return
    _shown = True
    QMessageBox.warning(
        parent,
        t("CapCut Online Service"),
        t(
            "CapCut Web and CapCut PC both use online services for speech recognition and voice generation. "
            "These services can be unstable and requests may time out."
        ),
    )
