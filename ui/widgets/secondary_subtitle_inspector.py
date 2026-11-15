"""TS2 inspector with its own selection, editors and subtitle data source."""
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDialog, QDialogButtonBox, QTableWidget, QTableWidgetItem, QMessageBox)
from ui.widgets.subtitle_text_fields import add_subtitle_text_fields
from i18n import t


class SecondarySubtitleInspector(QFrame):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.index = -1
        self.loading = False
        self.setObjectName("statusCard")
        self.setMinimumWidth(320)
        self.setMaximumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setSpacing(8)
        self.summary = QLabel()
        self.summary.setObjectName("subtitleSummaryBadge")
        self.duration = QLabel()
        self.duration.setObjectName("timingChip")
        header.addWidget(self.summary)
        header.addWidget(self.duration)
        header.addStretch(1)
        layout.addLayout(header)
        restore_row = QHBoxLayout()
        self.restore_button = QPushButton(t("Restore timing"))
        self.restore_button.setToolTip(t("Restore start and end from the last completed processing run."))
        self.restore_button.clicked.connect(lambda: controller.restore_timing(self.index))
        restore_row.addWidget(self.restore_button)
        restore_row.addStretch(1)
        layout.addLayout(restore_row)
        actions = QHBoxLayout()
        self.editor_button = QPushButton(t("Subtitle Editor"))
        self.import_button = QPushButton(t("Import SRT"))
        actions.addWidget(self.editor_button)
        actions.addWidget(self.import_button)
        actions.addStretch()
        layout.addLayout(actions)
        self.build_button = QPushButton(t("Create / Update secondary subtitles"))
        layout.addWidget(self.build_button)
        self.editor_button.clicked.connect(self.open_editor)
        self.import_button.clicked.connect(controller.import_secondary_srt)
        self.build_button.clicked.connect(controller.build)
        timing = QHBoxLayout()
        timing.setSpacing(12)
        self.start_badge = QLabel()
        self.end_badge = QLabel()
        for badge in (self.start_badge, self.end_badge):
            badge.setObjectName("timingChip")
            timing.addWidget(badge)
        timing.addStretch(1)
        layout.addLayout(timing)
        self.original = QLabel()
        self.original.setWordWrap(True)
        self.original.setObjectName("helperLabel")
        layout.addWidget(self.original)
        self.fields = add_subtitle_text_fields(layout, "", on_edit=self.edit_text,
            on_selection=self.selection_changed, on_highlight=self.add_highlight)
        self.editor = self.fields["translated_editor"]
        self.editor.setObjectName("secondarySegmentInspectorEditor")
        layout.addStretch(1)

    def refresh(self, index):
        rows = self.controller.timeline_rows(copy_rows=False)
        self.loading = True
        try:
            self.index = index if 0 <= index < len(rows) else -1
            valid = self.index >= 0
            self.editor.setEnabled(valid)
            self.restore_button.setEnabled(valid and self.controller.can_restore_timing(self.index))
            from app.subtitle_timing import baseline
            available = valid and baseline(rows[self.index]) is not None
            self.restore_button.setToolTip(t("Restore start and end from the last completed processing run.") if available else t("No completed-run timing is available for this cue yet."))
            self.build_button.setEnabled(not self.controller.busy)
            row = rows[self.index] if valid else dict(start=0., end=0., text="")
            self.summary.setText(f"#{index + 1} / {len(rows)}" if valid else "")
            self.summary.setVisible(valid)
            self.duration.setText(t("Duration") + f"  {max(0., row['end'] - row['start']):.3f}s" if valid else "")
            self.duration.setVisible(valid)
            self.start_badge.setText(t("Start  {time}", time=self.controller.gui.format_timestamp(row["start"])))
            self.end_badge.setText(t("End  {time}", time=self.controller.gui.format_timestamp(row["end"])))
            self.start_badge.setVisible(valid)
            self.end_badge.setVisible(valid)
            self.original.setText(row.get("source_text", ""))
            self.original.setVisible(bool(row.get("source_text")) and row["source_text"] != row["text"])
            if self.editor.toPlainText() != row["text"]:
                blocker = QSignalBlocker(self.editor)
                self.editor.setPlainText(row["text"])
                del blocker
            self.refresh_highlights(row.get("manual_highlights", []))
            self.selection_changed(self.editor)
        finally:
            self.loading = False

    def edit_text(self, editor):
        if self.loading or self.index < 0:
            return
        text = editor.toPlainText().strip()
        # Keep the last valid cue while the user temporarily clears the field.
        if text:
            self.controller.update_secondary(self.index, text=text)

    def selection_changed(self, editor):
        self.fields["highlight_button"].setEnabled(self.index >= 0 and bool(editor.textCursor().selectedText().strip()))

    def add_highlight(self, editor):
        phrase = " ".join(editor.textCursor().selectedText().split())
        rows = self.controller.secondary_rows()
        if self.index < 0 or self.index >= len(rows) or not phrase:
            return
        highlights = list(rows[self.index].get("manual_highlights", []))
        if phrase.casefold() not in {value.casefold() for value in highlights}:
            self.controller.update_secondary(self.index, manual_highlights=highlights + [phrase])

    def remove_highlight(self, phrase):
        rows = self.controller.secondary_rows()
        if 0 <= self.index < len(rows):
            self.controller.update_secondary(self.index, manual_highlights=[
                value for value in rows[self.index].get("manual_highlights", []) if value != phrase])

    def refresh_highlights(self, highlights):
        if tuple(highlights) == getattr(self, "_shown_highlights", None):
            return
        self._shown_highlights = tuple(highlights)
        layout = self.fields["highlight_chip_layout"]
        while layout.count():
            widget = layout.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()
        for phrase in highlights:
            button = QPushButton(phrase + " ×")
            button.clicked.connect(lambda _=False, phrase=phrase: self.remove_highlight(phrase))
            layout.addWidget(button)
        layout.addStretch()

    def open_editor(self):
        dialog = QDialog(self)
        dialog.setWindowTitle(t("Secondary subtitles"))
        dialog.resize(720, 440)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(0, 3, dialog)
        table.setHorizontalHeaderLabels([t("Start (s)"), t("End (s)"), t("Secondary subtitle")])
        table.horizontalHeader().setStretchLastSection(True)
        rows = self.controller.timeline_rows()
        table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, field in enumerate(("start", "end", "text")):
                table.setItem(i, j, QTableWidgetItem(str(row[field])))
        layout.addWidget(table)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.rejected.connect(dialog.reject)
        def save():
            try:
                edited = [dict(row, start=float(table.item(i, 0).text()), end=float(table.item(i, 1).text()),
                               text=table.item(i, 2).text()) for i, row in enumerate(rows)]
                self.controller.replace_timeline_rows(edited)
            except (ValueError, AttributeError) as exc:
                QMessageBox.warning(dialog, t("Secondary subtitles"), str(exc))
                return
            dialog.accept()
        buttons.accepted.connect(save)
        layout.addWidget(buttons)
        dialog.exec()
