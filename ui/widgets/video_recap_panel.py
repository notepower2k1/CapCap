from html import escape

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFormLayout, QHBoxLayout, QLabel, QListView, QListWidget,
    QPlainTextEdit, QPushButton, QTabWidget, QTextBrowser, QToolButton, QVBoxLayout, QWidget,
)

from i18n import t
from translation.video_recap import TARGET_SECONDS, estimate_seconds


class VideoRecapPanel(QWidget):
    generate_requested = Signal()
    edited = Signal()
    seek_requested = Signal(int)
    selection_changed = Signal()

    def __init__(self):
        super().__init__()
        self.setMinimumWidth(220)
        self.setMaximumWidth(320)
        self.draft = None
        self._scene_spans = []
        self._audio_durations = []
        self._evidence_is_current = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        controls = QHBoxLayout()
        duration_label = QLabel(t("Target duration"))
        self.duration_combo = QComboBox()
        duration_label.setBuddy(self.duration_combo)
        for seconds in TARGET_SECONDS:
            self.duration_combo.addItem(t("{count} min", count=seconds // 60), seconds)
        self.duration_combo.setCurrentIndex(len(TARGET_SECONDS) - 1)
        controls.addWidget(duration_label)
        controls.addWidget(self.duration_combo)
        self.generate_btn = QPushButton(t("Create recap script"))
        self.generate_btn.clicked.connect(self.generate_requested)
        controls.addStretch(1)
        layout.addLayout(controls)
        layout.addWidget(self.generate_btn)

        self.notice = QLabel()
        self.notice.setTextFormat(Qt.PlainText)
        self.notice.setWordWrap(True)
        self.notice.setObjectName("helperLabel")
        layout.addWidget(self.notice)

        self.setStyleSheet("""
            QListWidget, QPlainTextEdit {
                background: #111927;
                color: #e6eef9;
                border: 1px solid #30425b;
                border-radius: 8px;
            }
            QListWidget::item { padding: 5px 7px; }
            QListWidget::item:selected { background: #24425f; color: #f8fbff; }
        """)
        heading = QLabel(t("Scenes in the recap"))
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)
        self.segment_list = QListWidget()
        self.segment_list.setMinimumHeight(128)
        self.segment_list.setWordWrap(True)
        self.segment_list.setTextElideMode(Qt.ElideNone)
        self.segment_list.setResizeMode(QListView.Adjust)
        self.segment_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.segment_list.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.segment_list.currentRowChanged.connect(self._select_segment)
        layout.addWidget(self.segment_list, 1)
        layout.addStretch()

        self.inspector = QTabWidget()
        self.inspector.setMinimumWidth(305)
        self.inspector.setMaximumWidth(430)
        self.inspector.tabBar().setStyleSheet("QTabBar::tab { min-width: 0px; padding: 8px 9px; }")
        self.inspector.tabBar().setExpanding(True)
        script_page = QWidget()
        script = QVBoxLayout(script_page)
        script.setContentsMargins(10, 10, 10, 10)
        script.setSpacing(8)
        self.narration = QPlainTextEdit()
        self.narration.setPlaceholderText(t("Edit narration"))
        self.narration.setMinimumHeight(110)
        self.narration.textChanged.connect(self._edit_segment)
        self.narration.setStyleSheet("background: #111927; color: #e6eef9; border: 1px solid #30425b; border-radius: 8px;")
        script.addWidget(self.narration, 1)
        self.evidence_toggle = QToolButton()
        self.evidence_toggle.setText(t("Dialogue references"))
        self.evidence_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.evidence_toggle.setArrowType(Qt.RightArrow)
        self.evidence_toggle.setCheckable(True)
        self.evidence_toggle.setAutoRaise(True)
        self.evidence_toggle.hide()
        script.addWidget(self.evidence_toggle, 0, Qt.AlignLeft)
        self.evidence = QTextBrowser()
        self.evidence.setOpenLinks(False)
        self.evidence.setMaximumHeight(132)
        self.evidence.hide()
        self.evidence.anchorClicked.connect(self._seek)
        script.addWidget(self.evidence)
        self.evidence_toggle.toggled.connect(self._toggle_evidence)

        self.voice_controls = QWidget()
        voice_layout = QVBoxLayout(self.voice_controls)
        voice_layout.setContentsMargins(*script.getContentsMargins())
        voice_layout.setSpacing(script.spacing())
        self.voice_label = QLabel()
        self.voice_label.setTextFormat(Qt.PlainText)
        self.voice_label.setWordWrap(True)
        self.voice_label.setObjectName("helperLabel")
        filters = QFormLayout()
        filters.setContentsMargins(0, 0, 0, 0)
        filters.setHorizontalSpacing(12)
        filters.setVerticalSpacing(8)
        filters.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        filters.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        self.voice_engine_combo = QComboBox()
        self.voice_gender_combo = QComboBox()
        for label, value in (("Any", "any"), ("Male", "male"), ("Female", "female")):
            self.voice_gender_combo.addItem(t(label), value)
        self.recap_voice_combo = QComboBox()
        self.recap_voice_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.recap_voice_combo.setMinimumContentsLength(12)
        filters.addRow(t("Engine"), self.voice_engine_combo)
        filters.addRow(t("Gender"), self.voice_gender_combo)
        filters.addRow(t("Voice"), self.recap_voice_combo)
        voice_layout.addLayout(filters)
        voice_layout.addWidget(self.voice_label)
        actions = QHBoxLayout()
        self.generate_segment_voice_btn = QPushButton(t("Generate this clip"))
        self.generate_all_voice_btn = QPushButton(t("Generate recap narration"))
        self.play_voice_btn = QPushButton(t("Listen"))
        for button in (self.generate_segment_voice_btn, self.play_voice_btn):
            actions.addWidget(button, 1)
        voice_layout.addLayout(actions)
        self.voice_status = QLabel()
        self.voice_status.setTextFormat(Qt.PlainText)
        self.voice_status.setWordWrap(True)
        self.voice_status.setObjectName("helperLabel")
        voice_layout.addWidget(self.voice_status)
        voice_layout.addStretch()

        self.footer = QWidget()
        footer = QVBoxLayout(self.footer)
        footer.setContentsMargins(0, 0, 0, 0)
        self.estimate = QLabel()
        self.estimate.setWordWrap(True)
        footer.addWidget(self.estimate)
        self.copy_btn = QPushButton(t("Copy narration"))
        self.copy_btn.clicked.connect(self._copy)
        footer.addWidget(self.copy_btn)
        script.addWidget(self.footer)
        self.inspector.addTab(script_page, t("Narration"))
        self.inspector.addTab(self.voice_controls, t("Voice"))
        self.set_draft(None)

    def set_draft(self, draft):
        self.draft = draft
        self.evidence_toggle.setChecked(False)
        self._scene_spans = []
        self._audio_durations = []
        self.segment_list.clear()
        if draft is not None:
            duration_index = self.duration_combo.findData(draft["target_seconds"])
            self.duration_combo.setCurrentIndex(duration_index)
            for index, segment in enumerate(draft["segments"]):
                self.segment_list.addItem(self._segment_label(index, segment))
            self.segment_list.setCurrentRow(0)
        else:
            self.duration_combo.setCurrentIndex(len(TARGET_SECONDS) - 1)
        self.segment_list.setVisible(draft is not None)
        self.inspector.setEnabled(draft is not None)
        self.generate_btn.setText(t("Regenerate recap" if draft is not None else "Create recap script"))
        self._update_estimate()

    def update_state(self, *, summary_ready, busy, stale, evidence_is_current):
        self.generate_btn.setEnabled(summary_ready and not busy)
        self.narration.setReadOnly(busy)
        notices = []
        if self.draft and stale:
            notices.append(t("The transcript or summary has changed. Review the draft or regenerate it."))
        if not summary_ready:
            notices.append(t("Create or update the video summary to generate a recap."))
        self.notice.setText("\n".join(notices))
        self.notice.setVisible(bool(notices))
        if evidence_is_current != self._evidence_is_current:
            self._evidence_is_current = evidence_is_current
            self._show_evidence(self.segment_list.currentRow())

    def _segment_label(self, index, segment):
        preview = " ".join(segment["narration"].split())
        if len(preview) > 105:
            preview = preview[:104].rsplit(" ", 1)[0] + "…"
        title = t("Scene {number}", number=index + 1)
        if index < len(self._scene_spans):
            title += f"  ·  {self._scene_spans[index]}"
        if segment.get("audio") and index < len(self._audio_durations) and not self._audio_durations[index]:
            title += f"  ·  {t('Voice outdated')}"
        return f"{title}\n{preview}"

    def set_clip_durations(self, durations):
        self._audio_durations = durations
        self._refresh_scene_labels()

    def set_scene_spans(self, spans):
        self._scene_spans = spans
        self._refresh_scene_labels()

    def _refresh_scene_labels(self):
        if self.draft is None:
            return
        for index, segment in enumerate(self.draft["segments"]):
            item = self.segment_list.item(index)
            if item is not None:
                label = self._segment_label(index, segment)
                if label != item.text():
                    item.setText(label)

    def _select_segment(self, index):
        segment = self.draft["segments"][index] if self.draft is not None and index >= 0 else None
        blocked = self.narration.blockSignals(True)
        self.narration.setPlainText(segment["narration"] if segment else "")
        self.narration.blockSignals(blocked)
        self._show_evidence(index)
        self._update_estimate()
        self.selection_changed.emit()

    def _toggle_evidence(self, expanded):
        self.evidence_toggle.setArrowType(Qt.DownArrow if expanded else Qt.RightArrow)
        self.evidence.setVisible(expanded)

    def _show_evidence(self, index):
        quotes = self.draft["segments"][index]["evidence"] if self.draft is not None and index >= 0 else []
        self.evidence_toggle.setVisible(bool(quotes))
        if not quotes:
            self.evidence_toggle.setChecked(False)
            self.evidence.clear()
            return
        lines = []
        for quote in quotes:
            ms = quote["time_ms"]
            seconds = ms // 1000
            timestamp = f"{seconds // 60:02d}:{seconds % 60:02d}"
            if self._evidence_is_current:
                timestamp = f'<a href="seek:{ms}" style="color:#8ad7ff">{timestamp}</a>'
            lines.append(f'<p>{timestamp} · {escape(quote["text"])}</p>')
        self.evidence.setHtml("".join(lines))

    def _edit_segment(self):
        index = self.segment_list.currentRow()
        if self.draft is None or index < 0:
            return
        segment = self.draft["segments"][index]
        segment["narration"] = self.narration.toPlainText()
        self.segment_list.item(index).setText(self._segment_label(index, segment))
        self._update_estimate()
        self.edited.emit()

    def _update_estimate(self):
        if self.draft is None:
            self.estimate.clear()
            return
        index = self.segment_list.currentRow()
        if index < 0:
            self.estimate.clear()
            return
        segment = self.draft["segments"][index]
        seconds = estimate_seconds([segment])
        reading_time = t("Estimated reading time {time}", time=f"{seconds // 60:02d}:{seconds % 60:02d}")
        self.estimate.setText(t("{count} words", count=len(segment["narration"].split())) + " · " + reading_time)

    def _copy(self):
        if self.draft:
            QApplication.clipboard().setText("\n\n".join(
                segment["narration"].strip() for segment in self.draft["segments"] if segment["narration"].strip()
            ))

    def _seek(self, url):
        if self._evidence_is_current and url.scheme() == "seek" and url.path().isdigit():
            self.seek_requested.emit(int(url.path()))
