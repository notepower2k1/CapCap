from html import escape
import os
from threading import Event

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton, QTextBrowser, QWidget,
)

from i18n import t
from translation import TranslationOrchestrator
from translation.video_summary import transcript_snapshot
from widgets.translation_progress_dialog import TranslationProgressDialog, provider_label


class _SummarySignals(QObject):
    progress = Signal(str, int, int)
    completed = Signal(object, str)


class _SummaryJob(QRunnable):
    def __init__(self, source, provider, task):
        super().__init__()
        self.source = source
        self.provider = provider
        self.task = task
        self.canceled = Event()
        self.signals = _SummarySignals()

    def run(self):
        try:
            engine = TranslationOrchestrator()
            generate = engine.suggest_video_hooks if self.task == "hooks" else engine.summarize_video
            result = generate(
                self.source, override_provider=self.provider, progress=self.signals.progress.emit,
                is_canceled=self.canceled.is_set,
            )
        except Exception as exc:
            self.signals.completed.emit(None, str(exc))
        else:
            self.signals.completed.emit(result, "")


class VideoSummaryController(QObject):
    ARTIFACT = "video_summary"

    def __init__(self, gui, page, layout):
        super().__init__(gui)
        self.gui = gui
        self.page = page
        self.project_root = ""
        self.result = None
        self.job = None
        self.request = None
        self.progress_dialog = None
        self.message = ""
        self.stale = False
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.setInterval(100)
        self.refresh_timer.timeout.connect(self.refresh)

        actions = QHBoxLayout()
        self.generate_btn = QPushButton(t("Create summary"))
        self.generate_btn.setEnabled(False)
        self.generate_btn.clicked.connect(self.start)
        actions.addWidget(self.generate_btn, 1)
        self.copy_btn = QPushButton(t("Copy summary"))
        self.copy_btn.clicked.connect(self.copy)
        self.copy_btn.hide()
        actions.addWidget(self.copy_btn)
        layout.addLayout(actions)

        self.status = QLabel(t("Extract or import the original subtitles to summarize this video."))
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("helperLabel")
        layout.addWidget(self.status)
        self.result_view = QTextBrowser()
        self.result_view.setOpenLinks(False)
        self.result_view.setMinimumHeight(300)
        self.result_view.anchorClicked.connect(self.seek)
        self.result_view.hide()
        layout.addWidget(self.result_view, 1)
        self.hook_btn = QPushButton(t("Generate hook suggestions"))
        self.hook_btn.clicked.connect(self.start_hooks)
        self.hook_btn.hide()
        layout.addWidget(self.hook_btn)
        self.hook_list = QWidget()
        hook_rows = QGridLayout(self.hook_list)
        hook_rows.setContentsMargins(0, 0, 0, 0)
        hook_rows.setColumnStretch(0, 1)
        self.hook_labels = []
        for row in range(3):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            hook_rows.addWidget(label, row, 0)
            self.hook_labels.append(label)
            copy_btn = QPushButton(t("Copy"))
            copy_btn.clicked.connect(lambda _checked=False, item=label: QApplication.clipboard().setText(item.text()))
            hook_rows.addWidget(copy_btn, row, 1)
        self.hook_list.hide()
        layout.addWidget(self.hook_list)

    def schedule_refresh(self):
        if self.gui.left_panel_stack.currentWidget() is self.page:
            self.refresh_timer.start()

    def clear_project(self):
        if self.job is not None:
            self.job.canceled.set()
        self._close_progress()
        self.request = None
        self.project_root = ""
        self.message = ""
        self._show_result(None)

    def _load_project(self):
        state = self.gui.current_project_state
        root = state.project_root if state else ""
        if root == self.project_root:
            return
        self.clear_project()
        self.project_root = root
        if state is None:
            return
        try:
            payload = self.gui.project_service.load_json_artifact(state, self.ARTIFACT)
            if (isinstance(payload, dict) and payload.get("version") == 1
                    and isinstance(payload.get("transcript_digest"), str)):
                self._show_result(payload)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.gui.log(f"[Video Summary] Could not load saved result: {exc}")
            self._show_result(None)

    @Slot()
    def refresh(self):
        self._load_project()
        rows, digest = transcript_snapshot(self.gui.current_segments or [])
        self._update_status(rows, digest)

    def _update_status(self, rows, digest):
        busy = self.job is not None
        self.stale = self.result is not None and self.result["transcript_digest"] != digest
        self.generate_btn.setEnabled(bool(rows and self.project_root and not busy))
        self.generate_btn.setText(t("Summarize again") if self.result else t("Create summary"))
        self.hook_btn.setEnabled(bool(self.result and not self.stale and not busy))
        message = ""
        if self.message:
            message = self.message
        elif not rows:
            message = t("Extract or import the original subtitles to summarize this video.")
        elif not self.result:
            message = t("The summary uses the AI provider in Settings.")
        self._show_status(message)

    def _show_status(self, message):
        messages = []
        if self.stale:
            messages.append(t("Transcript has changed. Create a new summary to update the result."))
        if message:
            messages.append(message)
        self.status.setText("\n".join(messages))
        self.status.setVisible(bool(messages))

    @Slot()
    def start(self):
        self._start("summary")

    @Slot()
    def start_hooks(self):
        self._start("hooks")

    def _start(self, task):
        if self.job is not None:
            return
        self._load_project()
        rows, digest = transcript_snapshot(self.gui.current_segments or [])
        if not rows or not self.project_root:
            return
        hooks = task == "hooks"
        if hooks and (self.result is None or self.result["transcript_digest"] != digest):
            self._update_status(rows, digest)
            return
        provider = (os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER")
                    or str(self.gui.settings.value("translation_provider", "google_ai_studio"))).strip().lower()
        if provider in {"google", "bing"}:
            self.message = t("Select an AI provider in Settings to generate hooks." if hooks
                             else "Select an AI provider in Settings to summarize this video.")
            self._update_status(rows, digest)
            return
        root = self.project_root
        source = self.result if hooks else rows
        provider_name = provider_label(provider) or "AI"
        if hooks:
            title = t("Generate hook suggestions")
            question = t("Send the video summary to {provider} to generate hooks?", provider=provider_name)
        else:
            title = t("Summarize again") if self.result else t("Create summary")
            question = t("Send {count} original subtitle cues to {provider} to create a summary?",
                         count=len(rows), provider=provider_name)
        answer = QMessageBox.question(
            self.gui, title, question,
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self._load_project()
        if self.job is not None or self.project_root != root or (hooks and self.result is not source):
            return
        self.request = (root, digest, provider, task)
        self.job = _SummaryJob(source, provider, task)
        self.job.signals.progress.connect(self._on_progress)
        self.job.signals.completed.connect(self._on_completed)
        self.message = t("Generating hook suggestions…" if hooks else "Creating summary…")
        self._update_status(rows, digest)
        self.progress_dialog = TranslationProgressDialog(self.gui, provider=provider, video_task=task)
        self.progress_dialog.canceled.connect(self.cancel)
        self.progress_dialog.show()
        QThreadPool.globalInstance().start(self.job)

    @Slot()
    def cancel(self):
        if self.job is None or self.request is None:
            return
        hooks = self.request[3] == "hooks"
        self.job.canceled.set()
        self.request = None
        self._close_progress()
        self.message = t("Hook generation canceled." if hooks else "Summary canceled.")
        self.refresh()

    def _close_progress(self):
        dialog, self.progress_dialog = self.progress_dialog, None
        if dialog is not None:
            dialog.dispose()

    @Slot(str, int, int)
    def _on_progress(self, stage, completed, total):
        if self.request is None:
            return
        label = {
            "transcript": "Summarizing transcript…", "merge": "Combining summaries…",
            "hooks": "Generating hook suggestions…",
        }[stage]
        self.message = t(label)
        self._show_status(self.message)
        dialog = self.progress_dialog
        if dialog is not None:
            dialog.set_progress(completed, total, message=label)

    @Slot(object, str)
    def _on_completed(self, result, error):
        self._close_progress()
        self.job = None
        request, self.request = self.request, None
        self._load_project()
        if request is None or request[0] != self.project_root:
            self.refresh()
            return

        root, digest, provider, task = request
        if result is not None:
            if task == "hooks":
                payload = {**self.result, "hooks": result}
            else:
                payload = {"version": 1, "transcript_digest": digest, **result}
            try:
                state = self.gui.ensure_current_project(persist_presentation=False)
                if state is None or state.project_root != root:
                    self.refresh()
                    return
                self.gui.project_service.save_json_artifact(
                    state, self.ARTIFACT, "analysis/video_summary.json", payload,
                )
                self._show_result(payload)
            except (OSError, ValueError) as exc:
                error = str(exc)
        if error:
            self.gui.log(f"[Video Summary] Failed task={task} provider={provider}: {error}")
            self.message = t("Could not generate hooks. {error}" if task == "hooks"
                             else "Could not create the summary. {error}", error=error)
        else:
            self.message = ""
        self.refresh()

    def _show_result(self, result):
        if result is None:
            self.result = None
            self.result_view.clear()
        else:
            overview = escape(result["summary"])
            points = []
            for item in result["points"]:
                points.append(f'<li>{escape(item["text"])} {self._links(item)}</li>')
            body = f'<p>{overview}</p><h4>{t("Key points")}</h4><ul>{"".join(points)}</ul>'
            if result["people"]:
                people = []
                for person in result["people"]:
                    kind = t("Speaker") if person["kind"] == "speaker" else t("Mentioned")
                    role = escape(person["role"])
                    people.append(f'<li><b>{escape(person["name"])}</b> ({kind}) '
                                  f'{role} {self._links(person)}</li>')
                body += f'<h4>{t("People in the transcript")}</h4><ul>{"".join(people)}</ul>'
            self.result_view.setHtml(body)
            self.result = result
        self.result_view.setVisible(result is not None)
        self.copy_btn.setVisible(result is not None)
        self.hook_btn.setVisible(result is not None)
        hooks = result.get("hooks") if result else None
        if isinstance(hooks, list) and len(hooks) == 3 and all(isinstance(hook, str) for hook in hooks):
            for label, hook in zip(self.hook_labels, hooks):
                label.setText(hook)
            self.hook_list.show()
        else:
            self.hook_list.hide()

    @staticmethod
    def _links(item):
        links = []
        for ms in item["times_ms"]:
            seconds = ms // 1000
            label = f"{seconds // 60:02d}:{seconds % 60:02d}"
            links.append(f'<a href="seek:{ms}">{label}</a>')
        return " · ".join(links)

    def seek(self, url):
        if url.scheme() == "seek" and url.path().isdigit():
            self.gui.set_position(int(url.path()))

    def copy(self):
        QApplication.clipboard().setText(self.result_view.toPlainText())
