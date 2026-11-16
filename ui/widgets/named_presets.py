"""Reusable local library of named user presets."""
import json

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QWidget, QHBoxLayout, QComboBox, QPushButton, QInputDialog, QMessageBox
from i18n import t


class NamedPresets(QWidget):
    def __init__(self, parent, *, settings, key, capture, apply):
        super().__init__(parent)
        self.settings, self.key = settings, key
        self.capture, self.apply = capture, apply
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setMinimumContentsLength(12)
        self.save_button = QPushButton(t("Save preset"))
        self.delete_button = QPushButton(t("Delete preset"))
        layout.addWidget(self.combo, 1)
        layout.addWidget(self.save_button)
        layout.addWidget(self.delete_button)
        self.combo.activated.connect(self.load_selected)
        self.combo.currentIndexChanged.connect(lambda: self.delete_button.setEnabled(self.combo.currentData() is not None))
        self.save_button.clicked.connect(self.save_current)
        self.delete_button.clicked.connect(self.delete_selected)
        self.refresh()

    def read(self):
        try:
            data = json.loads(self.settings.value(self.key, "{}"))
            return {name: value for name, value in data.items() if isinstance(name, str) and isinstance(value, dict)} if isinstance(data, dict) else {}
        except (TypeError, ValueError):
            return {}

    def refresh(self, selected=None):
        blocker = QSignalBlocker(self.combo)
        self.combo.clear()
        self.combo.addItem(t("Saved presets…"), None)
        for name in sorted(self.read(), key=str.casefold):
            self.combo.addItem(name, name)
        self.combo.setCurrentIndex(max(0, self.combo.findData(selected)))
        del blocker
        self.delete_button.setEnabled(self.combo.currentData() is not None)

    def load_selected(self, *_):
        name = self.combo.currentData()
        value = self.read().get(name)
        if value is not None:
            try:
                self.apply(value)
            except (ValueError, TypeError, KeyError) as exc:
                QMessageBox.warning(self, t("Error"), str(exc))

    def save_current(self):
        value = self.capture()
        if value is None:
            return
        name, accepted = QInputDialog.getText(self, t("Save preset"), t("Preset name:"), text=self.combo.currentData() or "")
        name = name.strip()
        if not accepted or not name:
            return
        data = self.read()
        if name in data and QMessageBox.question(self, t("Save preset"), t("Replace preset {name}?", name=name)) != QMessageBox.Yes:
            return
        data[name] = value
        self.settings.setValue(self.key, json.dumps(data, ensure_ascii=False))
        self.refresh(name)

    def delete_selected(self):
        name = self.combo.currentData()
        if name is None or QMessageBox.question(self, t("Delete preset"), t("Delete preset {name}?", name=name)) != QMessageBox.Yes:
            return
        data = self.read()
        data.pop(name, None)
        self.settings.setValue(self.key, json.dumps(data, ensure_ascii=False))
        self.refresh()
