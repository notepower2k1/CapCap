"""Centralized translated-subtitle review and correction dialog."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


def _timecode(seconds: float) -> str:
    total_ms = max(0, int(round(float(seconds or 0.0) * 1000)))
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"


class SubtitleEditorDialog(QDialog):
    """Editable table that stages text-only subtitle changes until Update."""

    _DELETE_COLUMN = 5
    _TEXT_COLUMN = 4

    def __init__(self, parent, segments, on_update, on_rewrite=None):
        super().__init__(parent)
        self._original_segments = deepcopy(list(segments or []))
        self._on_update = on_update
        self._on_rewrite = on_rewrite
        self._matches: list[tuple[int, int, int]] = []
        self._match_index = -1

        self.setWindowTitle("Subtitle Editor")
        self.setModal(True)
        self.resize(1120, 700)
        self.setMinimumSize(860, 520)
        self.setStyleSheet(
            """
            QDialog { background: #0f1724; color: #e8f0fa; }
            QLabel { color: #c8d7e8; background: transparent; }
            QLineEdit, QTableWidget {
                background: #132033; color: #eff6ff; border: 1px solid #34506f;
                border-radius: 7px; padding: 6px;
            }
            QHeaderView::section { background: #1b2b41; color: #dcecff; padding: 7px; border: 0; }
            QTableWidget::item { background: #132033; color: #eff6ff; }
            QTableWidget::item:selected { background: #244d70; color: #ffffff; }
            QPushButton { background: #223a55; color: #f6fbff; border: 1px solid #3b5b7c; border-radius: 7px; padding: 7px 12px; font-weight: 600; }
            QPushButton:hover { background: #2b4a6b; }
            QPushButton#primary { background: #1871b7; border-color: #4caaf1; }
            QPushButton#danger { background: #5d2c37; border-color: #9c5061; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        title = QLabel("Subtitle Editor")
        title.setStyleSheet("font-size: 18px; font-weight: 700; color: #f5f9ff;")
        root.addWidget(title)
        hint = QLabel(
            "Review translated subtitles in one place. Update preserves timing, speakers, styles, and other metadata. "
            "Only changed lines need new TTS audio."
        )
        hint.setWordWrap(True)
        root.addWidget(hint)

        find_row = QHBoxLayout()
        find_row.addWidget(QLabel("Find"))
        self.find_edit = QLineEdit(self)
        self.find_edit.setPlaceholderText("Word, phrase, name, or terminology")
        find_row.addWidget(self.find_edit, 1)
        self.match_label = QLabel("0 matches")
        find_row.addWidget(self.match_label)
        previous_btn = QPushButton("Previous")
        next_btn = QPushButton("Next")
        find_row.addWidget(previous_btn)
        find_row.addWidget(next_btn)
        root.addLayout(find_row)

        replace_row = QHBoxLayout()
        replace_row.addWidget(QLabel("Replace with"))
        self.replace_edit = QLineEdit(self)
        self.replace_edit.setPlaceholderText("Replacement text")
        replace_row.addWidget(self.replace_edit, 1)
        replace_one_btn = QPushButton("Replace")
        replace_all_btn = QPushButton("Replace All")
        replace_row.addWidget(replace_one_btn)
        replace_row.addWidget(replace_all_btn)
        root.addLayout(replace_row)

        self.table = QTableWidget(len(self._original_segments), 6, self)
        self.table.setHorizontalHeaderLabels(["#", "Start", "End", "Original", "Translated text", "Delete"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.table.verticalHeader().setVisible(False)
        # Keep one stable row color.  Alternating white/default rows made
        # the light text unreadable in the dark editor theme.
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(False)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(1, 105)
        self.table.setColumnWidth(2, 105)
        self.table.setColumnWidth(3, 270)
        self.table.setColumnWidth(5, 62)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        delete_btn = QPushButton("Delete Selected")
        delete_btn.setObjectName("danger")
        undo_delete_btn = QPushButton("Restore Selected")
        rewrite_btn = QPushButton("AI Rewrite…")
        actions.addWidget(delete_btn)
        actions.addWidget(undo_delete_btn)
        actions.addWidget(rewrite_btn)
        actions.addStretch(1)
        cancel_btn = QPushButton("Cancel")
        update_btn = QPushButton("Update")
        update_btn.setObjectName("primary")
        actions.addWidget(cancel_btn)
        actions.addWidget(update_btn)
        root.addLayout(actions)

        self._populate()
        self.find_edit.textChanged.connect(self._refresh_matches)
        previous_btn.clicked.connect(lambda: self._select_match(-1))
        next_btn.clicked.connect(lambda: self._select_match(1))
        replace_one_btn.clicked.connect(self._replace_current)
        replace_all_btn.clicked.connect(self._replace_all)
        delete_btn.clicked.connect(lambda: self._set_selected_deleted(True))
        undo_delete_btn.clicked.connect(lambda: self._set_selected_deleted(False))
        rewrite_btn.clicked.connect(self._open_rewrite)
        cancel_btn.clicked.connect(self.reject)
        update_btn.clicked.connect(self._apply)

    def _populate(self):
        for row, segment in enumerate(self._original_segments):
            values = [
                str(row + 1),
                _timecode(segment.get("start", 0.0)),
                _timecode(segment.get("end", 0.0)),
                str(segment.get("source_text") or segment.get("original_text") or ""),
                str(segment.get("text") or ""),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col != self._TEXT_COLUMN:
                    item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setData(Qt.UserRole, row)
                self.table.setItem(row, col, item)
            delete_item = QTableWidgetItem()
            delete_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable)
            delete_item.setCheckState(Qt.Unchecked)
            self.table.setItem(row, self._DELETE_COLUMN, delete_item)

    def _is_deleted(self, row: int) -> bool:
        item = self.table.item(row, self._DELETE_COLUMN)
        return bool(item and item.checkState() == Qt.Checked)

    def _set_deleted(self, row: int, deleted: bool):
        item = self.table.item(row, self._DELETE_COLUMN)
        if item is None:
            return
        item.setCheckState(Qt.Checked if deleted else Qt.Unchecked)
        tint = QBrush(QColor("#6a3440")) if deleted else QBrush()
        for col in range(self.table.columnCount()):
            cell = self.table.item(row, col)
            if cell is not None:
                cell.setBackground(tint)

    def _set_selected_deleted(self, deleted: bool):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        for row in rows:
            self._set_deleted(row, deleted)

    def _refresh_matches(self):
        needle = self.find_edit.text()
        self._matches = []
        self._match_index = -1
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self._TEXT_COLUMN)
            if not item or self._is_deleted(row) or not needle:
                continue
            start = 0
            text = item.text()
            while True:
                found = text.lower().find(needle.lower(), start)
                if found < 0:
                    break
                self._matches.append((row, found, len(needle)))
                start = found + max(1, len(needle))
        self.match_label.setText(f"{len(self._matches)} match{'es' if len(self._matches) != 1 else ''}")
        if self._matches:
            self._select_match(1)

    def _select_match(self, direction: int):
        if not self._matches:
            return
        self._match_index = (self._match_index + direction) % len(self._matches)
        row, start, length = self._matches[self._match_index]
        item = self.table.item(row, self._TEXT_COLUMN)
        self.table.setCurrentCell(row, self._TEXT_COLUMN)
        self.table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.match_label.setText(f"{self._match_index + 1} / {len(self._matches)} matches")

    def _replace_current(self):
        if not self._matches:
            return
        if self._match_index < 0:
            self._select_match(1)
        row, start, length = self._matches[self._match_index]
        item = self.table.item(row, self._TEXT_COLUMN)
        item.setText(item.text()[:start] + self.replace_edit.text() + item.text()[start + length:])
        self._refresh_matches()

    def _replace_all(self):
        needle = self.find_edit.text()
        if not needle:
            return
        replaced = 0
        for row in range(self.table.rowCount()):
            if self._is_deleted(row):
                continue
            item = self.table.item(row, self._TEXT_COLUMN)
            text = item.text()
            # Case-insensitive replacement while retaining the rest of the cue.
            import re
            value, count = re.subn(re.escape(needle), self.replace_edit.text(), text, flags=re.IGNORECASE)
            if count:
                item.setText(value)
                replaced += count
        self._refresh_matches()
        self.match_label.setText(f"Replaced {replaced} occurrence{'s' if replaced != 1 else ''}")

    def _open_rewrite(self):
        if self._on_rewrite is None:
            return
        self.accept()
        self._on_rewrite()

    def _apply(self):
        rows = []
        for row, original in enumerate(self._original_segments):
            text_item = self.table.item(row, self._TEXT_COLUMN)
            rows.append({
                "original_index": row,
                "text": str(text_item.text() if text_item else "").strip(),
                "deleted": self._is_deleted(row),
                "original": original,
            })
        if all(row["deleted"] for row in rows):
            answer = QMessageBox.question(
                self, "Delete All Subtitles", "This removes every translated subtitle. Continue?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        if self._on_update(rows):
            self.accept()
