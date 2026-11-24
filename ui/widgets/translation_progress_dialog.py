"""Shared progress dialog for subtitle translation and video summaries."""
import os
import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QProgressDialog
from i18n import t


def provider_label(provider):
    return {
        "google_ai_studio": "Google AI Studio", "openai": "OpenAI", "ollama": "Ollama",
        "google": "Google Translate", "google-web": "Google Translate",
        "bing": "Bing Translator", "bing-web": "Bing Translator",
        "browser_ai": "ChatGPT Web" if os.getenv("CAPCAP_BROWSER_AI_PLATFORM", "gemini").lower() == "chatgpt" else "Gemini Web",
        "gemini_web": "Gemini Web", "chatgpt_web": "ChatGPT Web",
    }.get(provider, provider)


class TranslationProgressDialog(QProgressDialog):
    def __init__(self, parent, *, provider="", is_retranslation=False, secondary=False, video_task=""):
        super().__init__("", t("Cancel"), 0, 100, parent)
        self._started_at = time.monotonic()
        self._action = "Re-translating" if is_retranslation else "Translating"
        self._provider = provider_label(provider) or (
            parent._selected_ai_provider_label() if hasattr(parent, "_selected_ai_provider_label") else "AI")
        self._subject = "Secondary subtitles" if secondary else "Subtitles"
        self._progress_template = "{completed}/{total} cues ({percent}%)"
        self._duration_hint = "Large subtitle projects can take a few minutes."
        if video_task:
            self._progress_template = "{completed}/{total} steps ({percent}%)"
            if video_task in {"hooks", "recap", "recap_render"}:
                self._action = "Generating"
                self._subject = {"hooks": "Hook suggestions", "recap": "Recap script",
                                 "recap_render": "Recap video"}[video_task]
                self._duration_hint = ""
            else:
                self._action, self._subject = "Summarizing", "Video"
                self._duration_hint = "Summarizing long videos can take a few minutes."
        self._status_msg = self._progress_str = ""
        icon = getattr(parent, "light_window_icon", None)
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
            self._has_contrasting_popup_icon = True
        self.setWindowModality(Qt.NonModal)
        self.setAutoClose(False)
        self.setAutoReset(False)
        self.setMinimumDuration(0)
        self.setMinimumWidth(430)
        label = self.findChild(QLabel)
        if label is not None:
            label.setWordWrap(True)
        self.setValue(0)
        self.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; }"
            "QPushButton { background: #22344c; color: #e6eef9; border: 1px solid #36516f; border-radius: 6px; padding: 5px 14px; }")
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.refresh_label)
        self.timer.start()
        self.refresh_label()
        if hasattr(parent, "_register_progress_dialog"):
            parent._register_progress_dialog(self)

    def refresh_label(self):
        elapsed = int(time.monotonic() - self._started_at)
        notice = f"\n{t(self._status_msg)}" if self._status_msg else ""
        progress = f"{t('Progress')}: {self._progress_str}\n" if self._progress_str else ""
        hint = f"\n{t(self._duration_hint)}" if self._duration_hint else ""
        self.setWindowTitle(f"{t(self._action)} {t(self._subject)} ({self._provider})")
        self.setLabelText(
            f"{t(self._action)} {t(self._subject)} — {self._provider}...{notice}\n"
            f"{progress}{t('Elapsed')}: {elapsed // 60:02d}:{elapsed % 60:02d}{hint}")

    def set_status(self, provider, message=""):
        if provider:
            self._provider = provider_label(provider)
        if message:
            self._status_msg = message
        self.refresh_label()

    def set_progress(self, completed, total, *, message=""):
        if total <= 0:
            return
        if message:
            self._status_msg = message
        percent = max(0, min(100, int(completed * 100 / total)))
        self._progress_str = t(self._progress_template, completed=completed, total=total, percent=percent)
        self.setValue(percent)
        self.refresh_label()

    def dispose(self):
        self.timer.stop()
        parent = self.parentWidget()
        if hasattr(parent, "_unregister_progress_dialog"):
            parent._unregister_progress_dialog(self)
        self.hide()
        self.deleteLater()
