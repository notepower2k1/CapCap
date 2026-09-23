import os
import time

from PySide6.QtCore import Qt, QTimer, QSettings, QEventLoop
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QProgressDialog,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)

from worker_adapters import ContextExtractionWorker, RewriteTranslationWorker, TranscriptionWorker, TranslationWorker
from translation import (
    TranslationOrchestrator,
    clean_dialogue_context,
    get_preset_by_id,
    load_prompt_options,
    load_translation_presets,
    render_preset_prompt,
)

try:
    from i18n import t
except ImportError:
    from ui.i18n import t


class TranslationPromptDialog(QDialog):
    def __init__(self, parent, src_lang: str = "zh", target_lang: str = "vi"):
        super().__init__(parent)
        self.settings = getattr(parent, "settings", None) or QSettings("CapCap", "VideoTranslatorGUI")
        self.setWindowTitle(t("Translation Settings & Prompt Review"))
        self.setMinimumWidth(680)
        self.setMinimumHeight(240)
        self.src_lang = str(src_lang or "zh").strip().lower()
        self.target_lang = str(target_lang or "vi").strip().lower()

        self.selected_provider = "google_ai_studio"
        self.selected_batch_size = 80
        self.selected_prompt = ""
        self.selected_preset_id = "general_default"

        self._init_ui()
        self._apply_styles()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("Review Translation & AI Prompt")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)

        subtitle = QLabel("Review AI provider and prompt preset before translating.")
        subtitle.setObjectName("dialogSubtitle")
        layout.addWidget(subtitle)

        # Provider field
        provider_col = QVBoxLayout()
        provider_col.setSpacing(6)
        provider_label = QLabel("AI Provider (Configured in Settings):")
        provider_label.setObjectName("fieldLabel")
        provider_col.addWidget(provider_label)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Google AI Studio (Gemini)", "google_ai_studio")
        self.provider_combo.addItem("OpenAI", "openai")
        self.provider_combo.addItem("Ollama (Local)", "ollama")
        self.provider_combo.addItem("Google Translate (free, no key)", "google")
        self.provider_combo.addItem("Bing Translator (free, no key)", "bing")

        # Resolve active provider from Settings:
        current_env = (os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER") or "").strip().lower()
        if current_env == "gemini":
            current_env = "google_ai_studio"
        if current_env not in ("google_ai_studio", "openai", "ollama", "google", "bing"):
            current_env = str(self.settings.value("translation_provider", "")).strip().lower()
            if current_env == "gemini":
                current_env = "google_ai_studio"
        if current_env not in ("google_ai_studio", "openai", "ollama", "google", "bing"):
            current_env = "google_ai_studio"

        self.selected_provider = current_env
        idx_p = self.provider_combo.findData(current_env)
        if idx_p >= 0:
            self.provider_combo.setCurrentIndex(idx_p)
        else:
            self.provider_combo.setCurrentIndex(0)
        self.provider_combo.setEnabled(False)
        self.provider_combo.setToolTip(t("AI Provider is configured in Settings. Open Settings to change provider or API keys."))
        provider_col.addWidget(self.provider_combo)
        layout.addLayout(provider_col)

        # Provider note label
        self.provider_hint = QLabel()
        self.provider_hint.setObjectName("fieldHint")
        layout.addWidget(self.provider_hint)

        # Preset Selector
        self.preset_label = QLabel(t("Content / Prompt Preset:"))
        self.preset_label.setObjectName("fieldLabel")
        layout.addWidget(self.preset_label)

        self.preset_combo = QComboBox()
        presets = load_translation_presets()

        # Sort presets: matching source language first, then 'all', then others
        def preset_sort_key(item):
            langs = item.get("languages", [])
            if "all" in langs:
                return 1
            src_prefix = self.src_lang.split("-")[0]
            if any(l.startswith(src_prefix) for l in langs):
                return 0
            return 2

        sorted_presets = sorted(presets, key=preset_sort_key)
        for p in sorted_presets:
            p_id = p.get("id", "")
            p_name = p.get("name", p_id)
            self.preset_combo.addItem(p_name, p_id)

        env_preset = str(os.getenv("CAPCAP_TRANSLATION_PRESET_ID", "")).strip()
        saved_preset = env_preset or str(self.settings.value("translation_preset_id", "general_default")).strip()
        idx_preset = self.preset_combo.findData(saved_preset)
        if idx_preset >= 0:
            self.preset_combo.setCurrentIndex(idx_preset)
        elif self.preset_combo.count() > 0:
            self.preset_combo.setCurrentIndex(0)

        layout.addWidget(self.preset_combo)

        # Auto-detect dialogue context & pronouns checkbox
        self.auto_context_cb = QCheckBox(t("Auto-detect dialogue context & pronouns"))
        saved_auto = self.settings.value("auto_translation_context", os.getenv("CAPCAP_AUTO_TRANSLATION_CONTEXT", "1"))
        is_checked = str(saved_auto).strip().lower() not in ("0", "false", "no")
        self.auto_context_cb.setChecked(is_checked)
        self.auto_context_cb.setToolTip(
            t("AI analyzes the full script for short videos or the opening lines for long videos to build character, "
              "role, and two-way address_rules profiles for consistent pronouns.")
        )
        layout.addWidget(self.auto_context_cb)

        # Review character profiles & address rules checkbox
        self.review_context_cb = QCheckBox(t("Review character & pronoun rules before translating"))
        saved_review = self.settings.value("review_translation_context", os.getenv("CAPCAP_REVIEW_TRANSLATION_CONTEXT", "1"))
        is_review_checked = str(saved_review).strip().lower() not in ("0", "false", "no")
        self.review_context_cb.setChecked(is_review_checked)
        self.review_context_cb.setEnabled(self.auto_context_cb.isChecked())
        self.review_context_cb.setToolTip(
            t("Pause after AI analyzes characters and pronouns to let you review and adjust them before translating batches.")
        )
        self.auto_context_cb.toggled.connect(lambda checked: self.review_context_cb.setEnabled(checked))
        layout.addWidget(self.review_context_cb)

        # Prompt editor
        self.prompt_box_label = QLabel(t("System Prompt (Editable):"))
        self.prompt_box_label.setObjectName("fieldLabel")
        layout.addWidget(self.prompt_box_label)

        self.prompt_edit = QPlainTextEdit()
        layout.addWidget(self.prompt_edit, stretch=1)

        # Connect signals
        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_changed)

        self._on_provider_changed()
        self._on_preset_changed()

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        translate_btn = QPushButton("Translate")
        translate_btn.setObjectName("primaryBtn")
        translate_btn.setDefault(True)
        translate_btn.clicked.connect(self._on_translate)
        btn_row.addWidget(translate_btn)

        layout.addLayout(btn_row)

    def _on_provider_changed(self):
        provider = self.provider_combo.currentData() or "google_ai_studio"
        is_free_provider = provider in ("google", "bing")
        if hasattr(self, "preset_label"):
            self.preset_label.setVisible(not is_free_provider)
        if hasattr(self, "preset_combo"):
            self.preset_combo.setVisible(not is_free_provider)
        if hasattr(self, "auto_context_cb"):
            self.auto_context_cb.setVisible(not is_free_provider)
        if hasattr(self, "review_context_cb"):
            self.review_context_cb.setVisible(not is_free_provider)
        if hasattr(self, "prompt_box_label"):
            self.prompt_box_label.setVisible(not is_free_provider)
        if hasattr(self, "prompt_edit"):
            self.prompt_edit.setVisible(not is_free_provider)

        if provider == "google":
            self.provider_hint.setText(t("💡 Google Translate translates directly via web API (free, no key). It does not use LLM system prompts."))
            self.resize(self.width(), 240)
        elif provider == "bing":
            self.provider_hint.setText(t("💡 Bing Translator translates directly via web API (free, no key). It does not use LLM system prompts."))
            self.resize(self.width(), 240)
        else:
            self.provider_hint.setText("")
            self.prompt_edit.setEnabled(True)
            self.preset_combo.setEnabled(True)
            self.auto_context_cb.setEnabled(True)
            self.review_context_cb.setEnabled(self.auto_context_cb.isChecked())
            self.resize(self.width(), 600)

    def _on_preset_changed(self):
        preset_id = self.preset_combo.currentData() or "general_default"
        try:
            rendered = render_preset_prompt(
                preset_id,
                source_lang=self.src_lang,
                target_lang=self.target_lang,
            )
        except Exception as exc:
            rendered = (
                "You are an expert subtitle translator. Translate the given subtitles accurately, "
                f"naturally, and concisely into {self.target_lang}."
            )
        self._rendered_preset_text = rendered.strip()
        self.prompt_edit.setPlainText(rendered)

    def _on_translate(self):
        self.selected_provider = self.provider_combo.currentData() or "google_ai_studio"
        self.selected_preset_id = self.preset_combo.currentData() or "general_default"

        current_prompt = self.prompt_edit.toPlainText().strip()
        rendered_prompt = getattr(self, "_rendered_preset_text", "").strip()
        if current_prompt == rendered_prompt:
            self.selected_prompt = ""
            self.is_custom_prompt = False
        else:
            self.selected_prompt = current_prompt
            self.is_custom_prompt = True

        auto_val = "1" if self.auto_context_cb.isChecked() else "0"
        self.settings.setValue("auto_translation_context", auto_val)
        os.environ["CAPCAP_AUTO_TRANSLATION_CONTEXT"] = auto_val

        review_val = "1" if (self.review_context_cb.isChecked() and self.auto_context_cb.isChecked()) else "0"
        self.settings.setValue("review_translation_context", review_val)
        os.environ["CAPCAP_REVIEW_TRANSLATION_CONTEXT"] = review_val

        self.selected_auto_context = bool(self.auto_context_cb.isChecked())
        self.selected_review_context = bool(self.review_context_cb.isChecked() and self.auto_context_cb.isChecked())

        self.settings.setValue("translation_preset_id", self.selected_preset_id)
        os.environ["CAPCAP_TRANSLATION_PRESET_ID"] = self.selected_preset_id

        # Mirror to legacy CapCap namespace for compatibility across controllers
        legacy_s = QSettings("CapCap", "CapCap")
        legacy_s.setValue("translation_preset_id", self.selected_preset_id)
        legacy_s.setValue("auto_translation_context", auto_val)
        legacy_s.setValue("review_translation_context", review_val)

        self.accept()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background: #0f1724;
                color: #e8f0fa;
            }
            QLabel#dialogTitle {
                color: #f1f5f9;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#dialogSubtitle {
                color: #94a3b8;
                font-size: 12px;
                margin-bottom: 4px;
            }
            QLabel#fieldLabel {
                color: #cbd5e1;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel#fieldHint {
                color: #38bdf8;
                font-size: 12px;
            }
            QPlainTextEdit {
                background: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 12px;
            }
            QSpinBox {
                background: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 6px 10px;
                font-size: 13px;
            }
            QSpinBox:focus {
                border: 1px solid #38bdf8;
            }
            QComboBox {
                background: #1e293b;
                color: #f8fafc;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 13px;
            }
            QComboBox:focus {
                border: 1px solid #38bdf8;
            }
            QComboBox:disabled {
                background: #141f2e;
                color: #cbd5e1;
                border: 1px solid #28394e;
            }
            QComboBox QAbstractItemView {
                background: #1e293b;
                color: #f8fafc;
                selection-background-color: #0284c7;
                selection-color: #ffffff;
                border: 1px solid #334155;
            }
            QPushButton {
                background: #334155;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #475569;
            }
            QPushButton#primaryBtn {
                background: #0284c7;
                color: #ffffff;
                border: none;
                font-weight: 600;
                padding: 8px 24px;
            }
            QPushButton#primaryBtn:hover {
                background: #0369a1;
            }
        """)


class DialogueContextReviewDialog(QDialog):
    """Modal dialog allowing the user to review, edit, re-analyze, or skip character address rules."""

    def __init__(
        self,
        parent=None,
        initial_context: str = "",
        segments=None,
        src_lang: str = "zh-Hans",
        target_lang: str = "vi",
        provider: str = "",
        error_hint: str = "",
    ):
        super().__init__(parent)
        self.settings = getattr(parent, "settings", None) or QSettings("CapCap", "CapCap")
        self.setWindowTitle(t("Review Character Profiles & Addressing Rules"))
        self.setMinimumWidth(660)
        self.setMinimumHeight(500)
        self.resize(700, 520)

        self.segments = segments or []
        self.src_lang = str(src_lang or "zh-Hans")
        self.target_lang = str(target_lang or "vi")
        self.provider = str(provider or "")
        self.error_hint = str(error_hint or "").strip()

        self.result_context: str = initial_context or ""
        self.skipped: bool = False
        self._reanalyze_worker = None

        self._init_ui(initial_context)
        self._apply_styles()

    def _init_ui(self, initial_context: str):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 20, 20, 20)

        title_label = QLabel(t("Confirm & Edit Character Profiles & Addressing Rules"))
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        desc_label = QLabel(
            t(
                "AI has analyzed the dialogue cues and established the following character roles and pronoun rules.\n"
                "Please review and edit them if needed to ensure 100% correct addressing across all dialogue."
            )
        )
        desc_label.setObjectName("dialogSubtitle")
        desc_label.setWordWrap(True)
        layout.addWidget(desc_label)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setPlainText(clean_dialogue_context(initial_context) if initial_context else "")
        self.text_edit.setPlaceholderText(
            t("Enter character profiles and address rules (e.g. A calls B as anh, self as em)...")
        )
        layout.addWidget(self.text_edit, stretch=1)

        # Feedback section for guiding AI re-analysis
        feedback_box = QVBoxLayout()
        feedback_box.setSpacing(4)

        feedback_label = QLabel(t("Optional guidance / corrections for AI to re-analyze:"))
        feedback_label.setObjectName("dialogSubtitle")
        feedback_box.addWidget(feedback_label)

        feedback_row = QHBoxLayout()
        feedback_row.setSpacing(8)

        self.feedback_input = QLineEdit()
        self.feedback_input.setPlaceholderText(
            t("Enter tips or corrections (e.g. Triều Tịch is female; stalker calls mày-tao)...")
        )
        self.feedback_input.returnPressed.connect(self._on_reanalyze)
        feedback_row.addWidget(self.feedback_input, stretch=1)

        self.reanalyze_btn = QPushButton(t("🔄 Re-analyze with Feedback"))
        self.reanalyze_btn.setObjectName("reanalyzeBtn")
        self.reanalyze_btn.setToolTip(t("Re-run AI dialogue analysis incorporating your custom feedback or guidance."))
        self.reanalyze_btn.clicked.connect(self._on_reanalyze)
        feedback_row.addWidget(self.reanalyze_btn)

        feedback_box.addLayout(feedback_row)
        layout.addLayout(feedback_box)

        self.status_label = QLabel("")
        self.status_label.setObjectName("fieldHint")
        if not (initial_context and initial_context.strip()):
            if self.error_hint:
                self.status_label.setText(f"⚠️ {self.error_hint}")
            else:
                self.status_label.setText(t("⚠️ AI could not auto-detect characters. You can enter rules manually or click '🔄 Re-analyze'."))
        layout.addWidget(self.status_label)

        self.dont_show_again_cb = QCheckBox(
            t("Do not show this confirmation dialog again (can be re-enabled in Translation settings)")
        )
        layout.addWidget(self.dont_show_again_cb)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)

        self.skip_btn = QPushButton(t("Skip Rules"))
        self.skip_btn.setToolTip(t("Do not enforce any character pronoun rules for this translation."))
        self.skip_btn.clicked.connect(self._on_skip)
        btn_layout.addWidget(self.skip_btn)

        btn_layout.addStretch()

        self.cancel_btn = QPushButton(t("Cancel"))
        self.cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self.cancel_btn)

        self.continue_btn = QPushButton(t("Continue Translation"))
        self.continue_btn.setObjectName("primaryBtn")
        self.continue_btn.clicked.connect(self._on_continue)
        btn_layout.addWidget(self.continue_btn)

        layout.addLayout(btn_layout)

    def _persist_dont_show_preference(self):
        if self.dont_show_again_cb.isChecked():
            self.settings.setValue("review_translation_context", "0")
            os.environ["CAPCAP_REVIEW_TRANSLATION_CONTEXT"] = "0"
            legacy_s = QSettings("CapCap", "CapCap")
            legacy_s.setValue("review_translation_context", "0")

    def _on_continue(self):
        self._persist_dont_show_preference()
        self.result_context = self.text_edit.toPlainText().strip()
        self.skipped = False
        self.accept()

    def _on_skip(self):
        self._persist_dont_show_preference()
        self.result_context = "__SKIP__"
        self.skipped = True
        self.accept()

    def _on_reanalyze(self):
        if not self.segments:
            self.status_label.setText(t("No dialogue segments available to analyze."))
            return

        feedback = self.feedback_input.text().strip() if hasattr(self, "feedback_input") else ""
        existing_context = self.text_edit.toPlainText().strip() if hasattr(self, "text_edit") else ""
        self.reanalyze_btn.setEnabled(False)
        self.continue_btn.setEnabled(False)
        self.skip_btn.setEnabled(False)
        if feedback:
            self.status_label.setText(t("Re-analyzing dialogue context with user guidance..."))
        else:
            self.status_label.setText(t("Re-analyzing dialogue context..."))

        self._reanalyze_worker = ContextExtractionWorker(
            self.segments,
            self.src_lang,
            self.target_lang,
            self.provider,
            self,
            user_guidance=feedback,
            existing_context=existing_context,
        )

        def _on_ready(ctx: str):
            cleaned = clean_dialogue_context(ctx) if ctx else ""
            self.text_edit.setPlainText(cleaned)
            if cleaned and cleaned.strip():
                self.status_label.setText(t("✓ Re-analysis complete."))
            else:
                self.status_label.setText(t("⚠️ AI returned empty character rules."))
            self.reanalyze_btn.setEnabled(True)
            self.continue_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)

        def _on_fail(err: str):
            self.status_label.setText(f"{t('Re-analysis failed')}: {err}")
            self.reanalyze_btn.setEnabled(True)
            self.continue_btn.setEnabled(True)
            self.skip_btn.setEnabled(True)

        self._reanalyze_worker.context_ready.connect(_on_ready)
        self._reanalyze_worker.failed.connect(_on_fail)
        self._reanalyze_worker.start()

    def _apply_styles(self):
        self.setStyleSheet("""
            QDialog {
                background: #0f1724;
                color: #e8f0fa;
            }
            QLabel#dialogTitle {
                color: #f1f5f9;
                font-size: 16px;
                font-weight: bold;
            }
            QLabel#dialogSubtitle {
                color: #94a3b8;
                font-size: 12px;
                line-height: 1.4;
            }
            QLabel#fieldHint {
                color: #38bdf8;
                font-size: 12px;
            }
            QPlainTextEdit {
                background: #1e293b;
                color: #cbd5e1;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 10px;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 13px;
                line-height: 1.5;
            }
            QPlainTextEdit:focus {
                border: 1px solid #38bdf8;
            }
            QLineEdit {
                background: #1e293b;
                color: #f1f5f9;
                border: 1px solid #334155;
                border-radius: 6px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #38bdf8;
            }
            QCheckBox {
                color: #cbd5e1;
                font-size: 12px;
            }
            QPushButton {
                background: #334155;
                color: #f8fafc;
                border: 1px solid #475569;
                border-radius: 6px;
                padding: 8px 18px;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background: #475569;
            }
            QPushButton#reanalyzeBtn {
                background: #1e293b;
                color: #38bdf8;
                border: 1px solid #0284c7;
                font-weight: 600;
            }
            QPushButton#reanalyzeBtn:hover {
                background: #0369a1;
                color: #ffffff;
            }
            QPushButton#primaryBtn {
                background: #0284c7;
                color: #ffffff;
                border: none;
                font-weight: 600;
                padding: 8px 24px;
            }
            QPushButton#primaryBtn:hover {
                background: #0369a1;
            }
        """)


class SubtitleController:
    REWRITE_STYLE_PRESETS_FILE = "rewrite_style_presets.json"

    def __init__(self, gui):
        self.gui = gui

    def _show_translation_progress(self, *, is_retranslation: bool, provider_name: str = ""):
        """Show elapsed time for both first-time and repeated translation."""
        self._close_translation_progress()
        if provider_name:
            provider_map = {
                "google_ai_studio": "Google AI Studio",
                "openai": "OpenAI",
                "ollama": "Ollama",
                "google": "Google Translate",
                "google-web": "Google Translate",
                "bing": "Bing Translator",
                "bing-web": "Bing Translator",
            }
            provider = provider_map.get(provider_name, provider_name)
        else:
            provider = self.gui._selected_ai_provider_label() if hasattr(self.gui, "_selected_ai_provider_label") else "selected provider"
        action = "Re-translating" if is_retranslation else "Translating"
        action_text = t(action)
        dialog = QProgressDialog(
            f"{action_text} {t('subtitles with')} {provider}...\n{t('Elapsed')}: 00:00",
            None,
            0,
            100,
            self.gui,
        )
        if hasattr(self.gui, "light_window_icon") and self.gui.light_window_icon and not self.gui.light_window_icon.isNull():
            dialog.setWindowIcon(self.gui.light_window_icon)
            dialog._has_contrasting_popup_icon = True
        if hasattr(self.gui, "_register_progress_dialog"):
            self.gui._register_progress_dialog(dialog)
        dialog.setWindowTitle(f"{action_text} {t('Subtitles')}")
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(430)
        dialog.setValue(0)
        dialog.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; }"
            "QPushButton { background: #22344c; color: #e6eef9; border: 1px solid #36516f; border-radius: 6px; padding: 5px 14px; }"
        )
        started = time.monotonic()
        dialog._started_at = started
        dialog._action = action
        dialog._provider = provider
        dialog._fallback_msg = ""
        dialog._progress_str = ""
        timer = QTimer(dialog)

        def update_elapsed():
            elapsed = int(time.monotonic() - started)
            curr_action = getattr(dialog, "_action", action)
            curr_provider = getattr(dialog, "_provider", provider)
            fallback_msg = getattr(dialog, "_fallback_msg", "")
            msg_line = f"\n{t(fallback_msg)}" if fallback_msg else ""
            prog_line = f"{t('Progress')}: {t(dialog._progress_str)}\n" if getattr(dialog, "_progress_str", "") else ""
            dialog.setLabelText(
                f"{t(curr_action)} {t('subtitles with')} {curr_provider}...{msg_line}\n"
                f"{prog_line}"
                f"{t('Elapsed')}: {elapsed // 60:02d}:{elapsed % 60:02d}\n"
                f"{t('Large subtitle projects can take a few minutes.')}"
            )

        timer.setInterval(1000)
        timer.timeout.connect(update_elapsed)
        timer.start()
        self.gui._translation_progress_dialog = dialog
        self.gui._translation_progress_timer = timer
        dialog.show()

    def _close_translation_progress(self):
        timer = getattr(self.gui, "_translation_progress_timer", None)
        if timer is not None:
            timer.stop()
        self.gui._translation_progress_timer = None
        dialog = getattr(self.gui, "_translation_progress_dialog", None)
        self.gui._translation_progress_dialog = None
        if dialog is not None:
            dialog.hide()
            dialog.deleteLater()

    def _show_transcription_progress(self, *, engine_name: str = "whisper"):
        self._close_transcription_progress()
        engine_label = {
            "whisper": "Whisper STT",
            "capcut": "CapCut STT",
            "sensevoice": "SenseVoice STT",
        }.get(engine_name, "Speech Recognition")
        action = "Transcribing"
        action_text = t(action)
        dialog = QProgressDialog(
            f"{action_text} {t('audio with')} {engine_label}...\n{t('Elapsed')}: 00:00",
            None,
            0,
            100,
            self.gui,
        )
        if hasattr(self.gui, "light_window_icon") and self.gui.light_window_icon and not self.gui.light_window_icon.isNull():
            dialog.setWindowIcon(self.gui.light_window_icon)
            dialog._has_contrasting_popup_icon = True
        if hasattr(self.gui, "_register_progress_dialog"):
            self.gui._register_progress_dialog(dialog)
        dialog.setWindowTitle(t("Transcribing Audio"))
        dialog.setWindowModality(Qt.NonModal)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setMinimumDuration(0)
        dialog.setMinimumWidth(440)
        dialog.setValue(0)
        dialog.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; }"
            "QPushButton { background: #22344c; color: #e6eef9; border: 1px solid #36516f; border-radius: 6px; padding: 5px 14px; }"
        )
        started = time.monotonic()
        dialog._started_at = started
        dialog._action = action
        dialog._engine_label = engine_label
        dialog._progress_str = ""
        timer = QTimer(dialog)

        def update_elapsed():
            elapsed = int(time.monotonic() - started)
            prog_line = f"{t('Progress')}: {t(dialog._progress_str)}\n" if getattr(dialog, "_progress_str", "") else ""
            h = elapsed // 3600
            m = (elapsed % 3600) // 60
            s = elapsed % 60
            elapsed_str = f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"
            dialog.setLabelText(
                f"{action_text} {t('audio with')} {engine_label}...\n"
                f"{prog_line}"
                f"{t('Elapsed')}: {elapsed_str}\n"
                f"{t('Long videos may take several minutes to transcribe.')}"
            )

        timer.setInterval(1000)
        timer.timeout.connect(update_elapsed)
        timer.start()
        self.gui._transcription_progress_dialog = dialog
        self.gui._transcription_progress_timer = timer
        dialog.show()

    def _close_transcription_progress(self):
        timer = getattr(self.gui, "_transcription_progress_timer", None)
        if timer is not None:
            timer.stop()
        self.gui._transcription_progress_timer = None
        dialog = getattr(self.gui, "_transcription_progress_dialog", None)
        self.gui._transcription_progress_dialog = None
        if dialog is not None:
            dialog.hide()
            dialog.deleteLater()

    def on_transcription_progress(self, percent: int, message: str):
        dialog = getattr(self.gui, "_transcription_progress_dialog", None)
        if dialog is not None:
            dialog.setValue(max(0, min(100, int(percent))))
            dialog._progress_str = t(str(message or ""))
        if hasattr(self.gui, "progress_bar"):
            scaled_value = 40 + int(percent * 0.2)
            self.gui.progress_bar.setValue(min(60, max(40, scaled_value)))
        if hasattr(self.gui, "transcript_text"):
            self.gui.transcript_text.setText(f"{t(message)}\n\n{t('Please wait a moment...')}")

    def run_transcription(self):
        audio_src = self.gui.audio_source_edit.text()
        if not audio_src or not os.path.exists(audio_src):
            QMessageBox.warning(self.gui, t("Error"), t("Audio source file not found! Please extract audio first."))
            return

        model_path = self.gui.get_whisper_model_path()
        lang = self.gui.get_source_language_code()

        self.gui.transcript_text.setText(t("Transcribing... please wait (Loading...)"))
        self.gui.transcribe_btn.setEnabled(False)
        self.gui.progress_bar.setValue(40)
        self.gui.update_project_step("transcribe", "running")

        engine_name = self.gui.get_transcription_engine()
        self._show_transcription_progress(engine_name=engine_name)
        self.gui.transcription_thread = TranscriptionWorker(audio_src, model_path, lang, engine_name=engine_name)
        self.gui.transcription_thread.progress.connect(self.on_transcription_progress)
        self.gui.transcription_thread.finished.connect(self.gui.on_transcription_finished)
        self.gui.transcription_thread.start()

    def on_transcription_finished(self, segments, error=""):
        self._close_transcription_progress()
        self.gui.transcribe_btn.setEnabled(True)
        if error or not segments:
            self.gui.update_project_step("transcribe", "failed")
            if error:
                self.gui.show_error(
                    t("Transcription Failed"),
                    t("Could not transcribe the audio."),
                    error,
                )
            else:
                QMessageBox.warning(self.gui, t("Warning"), t("Transcription failed or returned no results."))
            self.gui._pipeline_fail(t("Transcription failed."))
            return

        self.gui.current_segments = segments
        self.gui.current_translated_segments = []
        self.gui.progress_bar.setValue(60)
        self.gui.apply_segments_to_timeline()

        srt_text = self.gui.format_to_srt(segments)
        self.gui.transcript_text.setText(srt_text)

        video_path = self.gui.video_path_edit.text()
        if video_path:
            file_basename = os.path.splitext(os.path.basename(video_path))[0]
            out_folder = self.gui.get_project_temp_dir("subtitle")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_original.srt")
            from subtitle_builder import generate_srt

            generate_srt(segments, out_path)
            self.gui.last_original_srt_path = out_path
            self.gui.processed_artifacts["srt_original"] = out_path
            self.gui.persist_transcription_project_data(segments, out_path)
            QMessageBox.information(self.gui, t("Success"), f"{t('Transcription completed!')}\n{t('Original SRT saved to:')} {out_path}")
        else:
            self.gui.persist_transcription_project_data(segments)
            QMessageBox.information(self.gui, t("Success"), t("Transcription completed!"))

        self.gui.refresh_ui_state()
        self.gui.schedule_auto_frame_preview()
        # The OCR crop is only an editor aid. Do not leave it covering the
        # preview after a normal OCR transcript has completed.
        if self.gui.get_transcription_engine() == "ocr":
            self.gui.toggle_ocr_overlay_visibility(False)
            self.gui.log("[OCR Region] Hidden after OCR transcription completed.")
        self.gui._pipeline_advance("transcription")

    def run_translation(self, show_prompt_dialog: bool = True):
        existing = getattr(self.gui, "translation_thread", None)
        if existing is not None and existing.isRunning():
            self.gui.log("[Translation] A translation request is already running.")
            return
        srt_source = self.gui.transcript_text.toPlainText()
        if not srt_source or not srt_source.strip():
            QMessageBox.warning(self.gui, t("Error"), t("No transcription available to translate!"))
            return

        state = self.gui.ensure_current_project()
        translation_signature = self.gui.build_current_translation_signature()
        if state and translation_signature:
            cached_signature = str(state.settings.get("translation_signature", "") or "").strip()
            cached_translation_path = str(state.artifacts.get("translation_final", "") or "").strip()
            if cached_signature == translation_signature and cached_translation_path and os.path.exists(cached_translation_path):
                if not (self.gui.current_translated_segments or self.gui.translated_text.toPlainText().strip()):
                    self.gui.load_project_context(state)
                self.gui.progress_bar.setValue(100)
                self.gui.refresh_ui_state()
                msg_box = QMessageBox(self.gui)
                msg_box.setWindowTitle(t("Existing Translation Found"))
                msg_box.setText(
                    f"{t('Vietnamese subtitles already exist and the original transcript has not changed.')}\n\n"
                    f"{t('Would you like to reuse the existing translation (saves time and AI tokens), or re-translate from scratch with AI?')}"
                )
                msg_box.setIcon(QMessageBox.Question)
                btn_reuse = msg_box.addButton(t("Use Existing (Recommended)"), QMessageBox.AcceptRole)
                btn_retranslate = msg_box.addButton(t("Re-translate with AI"), QMessageBox.ActionRole)
                msg_box.setDefaultButton(btn_reuse)
                msg_box.exec()

                if msg_box.clickedButton() == btn_retranslate:
                    state.set_setting("translation_signature", "")
                    state.set_step_status("translate_raw", "pending")
                    state.set_step_status("refine_translation", "pending")
                    if hasattr(self.gui, "project_service"):
                        self.gui.project_service.save_project(state)
                    self.gui.log("[Translation] Re-translate confirmed by user; bypassing cache.")
                else:
                    self.gui.log("[Translation] Reused existing translation result.")
                    return

        src_lang = self.gui.get_source_language_code()
        target_lang = self.gui.get_target_language_code()

        chosen_provider = ""
        chosen_batch_size = None
        chosen_prompt = ""
        chosen_auto_context = True
        chosen_review_context = False

        if show_prompt_dialog:
            try:
                dialog = TranslationPromptDialog(self.gui, src_lang=src_lang, target_lang=target_lang)
                if dialog.exec() != QDialog.Accepted:
                    self.gui.log("[Translation] Translation canceled by user.")
                    return
                chosen_provider = dialog.selected_provider
                chosen_batch_size = getattr(dialog, "selected_batch_size", None)
                chosen_prompt = dialog.selected_prompt
                chosen_auto_context = getattr(dialog, "selected_auto_context", True)
                chosen_review_context = getattr(dialog, "selected_review_context", False)
            except Exception as exc:
                self.gui.log(f"[Translation] Warning: Could not open translation settings dialog ({exc}). Using defaults.")
                import traceback
                traceback.print_exc()
        else:
            settings = getattr(self.gui, "settings", None) or QSettings("CapCap", "CapCap")
            saved_auto = settings.value("auto_translation_context", os.getenv("CAPCAP_AUTO_TRANSLATION_CONTEXT", "1"))
            chosen_auto_context = str(saved_auto).strip().lower() not in ("0", "false", "no")
            saved_review = settings.value("review_translation_context", os.getenv("CAPCAP_REVIEW_TRANSLATION_CONTEXT", "1"))
            chosen_review_context = chosen_auto_context and (str(saved_review).strip().lower() not in ("0", "false", "no"))
            chosen_provider = (
                os.getenv("OPENAI_PROVIDER")
                or os.getenv("AI_POLISHER_PROVIDER")
                or str(settings.value("translation_provider", "google")).strip().lower()
            )

        self.gui._last_translation_provider = chosen_provider
        if chosen_provider == "google":
            chosen_auto_context = False
            chosen_review_context = False

        chosen_preset_id = os.getenv("CAPCAP_TRANSLATION_PRESET_ID") or "general_default"
        has_diarization = False
        speaker_count = 0
        source_segments = None
        if hasattr(self.gui, "current_segments") and self.gui.current_segments:
            source_segments = [
                seg.to_original_subtitle_dict() if hasattr(seg, "to_original_subtitle_dict")
                else (seg if isinstance(seg, dict) else getattr(seg, "__dict__", {}))
                for seg in self.gui.current_segments
            ]
        if not source_segments and srt_source:
            from translation.srt_utils import parse_srt
            source_segments = parse_srt(srt_source)

        if source_segments:
            speakers = {
                str(s.get("metadata", {}).get("speaker") or s.get("speaker") or "").strip()
                for s in source_segments
            }
            speakers.discard("")
            if speakers:
                has_diarization = True
                speaker_count = len(speakers)

        confirmed_context = ""
        if chosen_provider != "google" and chosen_auto_context and chosen_review_context and source_segments:
            confirmed_context = self._extract_and_review_context(
                source_segments=source_segments,
                src_lang=src_lang,
                target_lang=target_lang,
                provider=chosen_provider,
            )
            if confirmed_context is None:
                # User canceled during context extraction or review
                return

        diarize_label = f"ON ({speaker_count} speakers)" if has_diarization else "OFF"
        prompt_label = f"'{chosen_preset_id}' (Customized)" if chosen_prompt else f"'{chosen_preset_id}'"
        self.gui.log(f"[Translation] Prompt: {prompt_label} | Provider: '{chosen_provider or 'default'}' | Speaker Diarization: {diarize_label}")

        model_path = None
        enable_polish = False if chosen_provider == "google" else bool(self.gui.is_ai_polish_enabled())
        is_retranslation = bool(
            self.gui.current_translated_segments or self.gui.translated_text.toPlainText().strip()
        )
        self.gui.translated_text.setText(t("Translating with the selected provider... please wait."))
        self.gui.translate_btn.setEnabled(False)
        self.gui.progress_bar.setValue(80)
        self.gui.update_project_step("translate_raw", "running")
        self._show_translation_progress(is_retranslation=is_retranslation, provider_name=chosen_provider)

        self.gui.translation_thread = TranslationWorker(
            srt_source,
            model_path,
            src_lang,
            target_lang,
            enable_polish,
            provider=chosen_provider,
            batch_size=chosen_batch_size,
            custom_prompt=chosen_prompt,
            segments=source_segments,
            context_guidance=confirmed_context,
        )
        self.gui.translation_thread.finished.connect(self.gui.on_translation_finished)
        self.gui.translation_thread.progress.connect(self.on_translation_progress)
        self.gui.translation_thread.batch_ready.connect(self.on_translation_batch_ready)
        self.gui.translation_thread.status_changed.connect(self.on_translation_status_changed)
        self.gui.translation_thread.start()

    def _extract_and_review_context(
        self,
        source_segments: list[dict],
        src_lang: str,
        target_lang: str,
        provider: str,
    ) -> str | None:
        """Extract dialogue context via background worker with progress dialog, then open review dialog.

        Returns:
            - str (rules or empty or "__SKIP__") if user confirms / skips.
            - None if user canceled (aborts translation).
        """
        if not source_segments:
            return ""

        progress = QProgressDialog(
            t("Analyzing dialogue & establishing pronoun rules..."),
            t("Cancel"),
            0,
            0,
            self.gui,
        )
        if hasattr(self.gui, "light_window_icon") and self.gui.light_window_icon and not self.gui.light_window_icon.isNull():
            progress.setWindowIcon(self.gui.light_window_icon)
            progress._has_contrasting_popup_icon = True
        if hasattr(self.gui, "_register_progress_dialog"):
            self.gui._register_progress_dialog(progress)
        progress.setWindowTitle(t("Dialogue Analysis"))
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.setStyleSheet(
            "QProgressDialog { background-color: #101826; color: #e6eef9; }"
            "QLabel { color: #e6eef9; }"
            "QPushButton { background: #22344c; color: #e6eef9; border: 1px solid #36516f; border-radius: 6px; padding: 5px 14px; }"
        )

        extracted = {"text": "", "error": "", "done": False, "canceled": False}

        worker = ContextExtractionWorker(source_segments, src_lang, target_lang, provider, parent=self.gui)

        def on_ready(ctx: str):
            extracted["text"] = ctx
            extracted["done"] = True
            try:
                progress.canceled.disconnect(on_canceled)
            except Exception:
                pass
            progress.close()

        def on_failed(err: str):
            extracted["error"] = err
            extracted["done"] = True
            try:
                progress.canceled.disconnect(on_canceled)
            except Exception:
                pass
            progress.close()

        def on_canceled():
            if not extracted["done"]:
                extracted["canceled"] = True

        worker.context_ready.connect(on_ready)
        worker.failed.connect(on_failed)
        progress.canceled.connect(on_canceled)

        worker.start()
        progress.show()

        while not extracted["done"] and not extracted["canceled"]:
            QApplication.processEvents(QEventLoop.AllEvents, 50)
            if not worker.isRunning() and not extracted["done"]:
                QApplication.processEvents(QEventLoop.AllEvents, 50)
                break

        if extracted["canceled"] and not extracted["done"]:
            self.gui.log("[Translation] Pronoun context extraction canceled by user.")
            return None

        if extracted["error"]:
            self.gui.log(f"[Translation] Pronoun rules analysis notice: {extracted['error']}")

        # Step 2: Open DialogueContextReviewDialog
        initial_text = extracted["text"]
        review_dialog = DialogueContextReviewDialog(
            parent=self.gui,
            initial_context=initial_text,
            segments=source_segments,
            src_lang=src_lang,
            target_lang=target_lang,
            provider=provider,
            error_hint=extracted.get("error", ""),
        )
        if review_dialog.exec() != QDialog.Accepted:
            self.gui.log("[Translation] Translation canceled during pronoun rules review.")
            return None

        if review_dialog.skipped:
            self.gui.log("[Translation] Pronoun rules skipped by user.")
            return "__SKIP__"

        result = review_dialog.result_context or ""
        self.gui.log(f"[Translation] Confirmed pronoun rules ({len(result.splitlines())} lines).")
        return result

    def on_translation_status_changed(self, provider: str, message: str = ""):
        provider_map = {
            "google_ai_studio": "Google AI Studio",
            "openai": "OpenAI",
            "ollama": "Ollama",
            "google": "Google Translate",
            "google-web": "Google Translate",
            "bing": "Bing Translator",
            "bing-web": "Bing Translator",
        }
        display_provider = provider_map.get(provider, provider)
        dialog = getattr(self.gui, "_translation_progress_dialog", None)
        if dialog is not None:
            dialog._provider = display_provider
            if message:
                dialog._fallback_msg = message
            action = getattr(dialog, "_action", "Translating")
            action_text = t(action)
            dialog.setWindowTitle(f"{action_text} {t('Subtitles')} ({display_provider})")
            elapsed = int(time.monotonic() - getattr(dialog, "_started_at", time.monotonic()))
            fallback_msg = getattr(dialog, "_fallback_msg", "")
            msg_line = f"\n{t(fallback_msg)}" if fallback_msg else ""
            prog_line = f"{t('Progress')}: {t(dialog._progress_str)}\n" if getattr(dialog, "_progress_str", "") else ""
            dialog.setLabelText(
                f"{action_text} {t('subtitles with')} {display_provider}...{msg_line}\n"
                f"{prog_line}"
                f"{t('Elapsed')}: {elapsed // 60:02d}:{elapsed % 60:02d}\n"
                f"{t('Large subtitle projects can take a few minutes.')}"
            )

    def on_translation_progress(self, completed: int, total: int):
        if total <= 0:
            return
        pct = max(0, min(100, int(completed * 100 / total)))
        dialog = getattr(self.gui, "_translation_progress_dialog", None)
        if dialog is not None:
            dialog._progress_str = t("{completed}/{total} cues ({percent}%)", completed=completed, total=total, percent=pct)
            dialog.setValue(pct)
            elapsed = int(time.monotonic() - getattr(dialog, "_started_at", time.monotonic()))
            action = getattr(dialog, "_action", "Translating")
            provider = getattr(dialog, "_provider", "AI")
            fallback_msg = getattr(dialog, "_fallback_msg", "")
            msg_line = f"\n{t(fallback_msg)}" if fallback_msg else ""
            dialog.setLabelText(
                f"{t(action)} {t('subtitles with')} {provider}...{msg_line}\n"
                f"{t('Progress')}: {t(dialog._progress_str)}\n"
                f"{t('Elapsed')}: {elapsed // 60:02d}:{elapsed % 60:02d}\n"
                f"{t('Large subtitle projects can take a few minutes.')}"
            )
        if hasattr(self.gui, "progress_bar"):
            scaled = 80 + int(completed * 20 / total)
            self.gui.progress_bar.setValue(min(99, scaled))

    def on_translation_batch_ready(self, start_idx: int, batch_segments: list):
        if not batch_segments:
            return
        if hasattr(self.gui, "apply_partial_translation_batch"):
            try:
                self.gui.apply_partial_translation_batch(start_idx, batch_segments)
            except Exception as e:
                print(f"[Translation] Error applying partial batch: {e}")

    def on_translation_finished(self, translated_srt, error, fallback_notice=""):
        self._close_translation_progress()
        self.gui.translate_btn.setEnabled(True)
        if error or not translated_srt:
            self.gui.update_project_step("translate_raw", "failed")
            self.gui.show_error(
                t("Translation Failed"),
                t("Could not complete the Vietnamese translation."),
                error or t("The translator API returned an empty result."),
            )
            self.gui._pipeline_fail(t("Translation failed."))
            return

        self.gui.progress_bar.setValue(100)
        self.gui.translated_text.setText(translated_srt)
        self.gui.apply_edited_translation(show_message=False, force_apply=True)

        fallback_text = ""
        if fallback_notice:
            if "BING_FALLBACK" in fallback_notice:
                self.gui._last_translation_provider = "bing"
                fallback_text = t("Translation completed using Bing Translator (AI Provider unavailable).")
            else:
                self.gui._last_translation_provider = "google"
                fallback_text = t("Translation completed using Google Translate (AI Provider unavailable).")

        video_path = self.gui.video_path_edit.text()
        if video_path:
            file_basename = os.path.splitext(os.path.basename(video_path))[0]
            out_folder = self.gui.get_project_temp_dir("subtitle")
            os.makedirs(out_folder, exist_ok=True)
            out_path = os.path.join(out_folder, file_basename + "_vi.srt")
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(translated_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
            message = f"{t('Process complete! Subtitle saved and loaded for preview:')}\n{out_path}"
            if fallback_text:
                message = f"{fallback_text}\n\n{message}"
            QMessageBox.information(self.gui, t("Finished"), message)
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)
            message = fallback_text if fallback_text else t("Translation complete!")
            QMessageBox.information(self.gui, t("Finished"), message)

        if fallback_notice:
            self.gui.log(f"[Translation] {fallback_notice}")

        self.gui.refresh_ui_state()
        self.gui._pipeline_advance("translation")

    def _collapse_translated_segments_for_rewrite(self, source_segments, translated_segments):
        source_segments = list(source_segments or [])
        translated_segments = list(translated_segments or [])
        if not translated_segments:
            return []
        if len(source_segments) == len(translated_segments):
            collapsed = []
            for idx, seg in enumerate(translated_segments):
                item = dict(seg)
                if idx < len(source_segments):
                    item["source_text"] = source_segments[idx].get("source_text") or source_segments[idx].get("text", "")
                collapsed.append(item)
            return collapsed

        collapsed = []
        idx = 0
        while idx < len(translated_segments):
            seg = dict(translated_segments[idx] or {})
            group_id = str(seg.get("tts_group_id", "") or "").strip()
            group_items = [seg]
            idx += 1
            if group_id:
                while idx < len(translated_segments):
                    candidate = dict(translated_segments[idx] or {})
                    if str(candidate.get("tts_group_id", "") or "").strip() != group_id:
                        break
                    group_items.append(candidate)
                    idx += 1
            base_text = ' '.join(str(group_items[0].get("tts_text") or "").split()).strip()
            if not base_text:
                base_text = ' '.join(' '.join(str(item.get("text") or "").split()).strip() for item in group_items).strip()
            collapsed.append({
                "start": float(group_items[0].get("tts_group_start", group_items[0].get("start", 0.0)) or group_items[0].get("start", 0.0)),
                "end": float(group_items[-1].get("tts_group_end", group_items[-1].get("end", 0.0)) or group_items[-1].get("end", 0.0)),
                "text": base_text,
                "tts_text": base_text,
                "tts_group_id": group_id,
                "tts_group_start": float(group_items[0].get("tts_group_start", group_items[0].get("start", 0.0)) or group_items[0].get("start", 0.0)),
                "tts_group_end": float(group_items[-1].get("tts_group_end", group_items[-1].get("end", 0.0)) or group_items[-1].get("end", 0.0)),
                "words": list(group_items[0].get("words", []) or []),
                "manual_highlights": list(group_items[0].get("manual_highlights", []) or []),
            })
        if len(collapsed) == len(source_segments):
            for i, seg in enumerate(collapsed):
                seg["source_text"] = source_segments[i].get("source_text") or source_segments[i].get("text", "")
        return collapsed

    def _expand_rewrite_segments_for_current_layout(self, rewritten_segments, source_segments):
        normalized = []
        source_segments = list(source_segments or [])
        for idx, seg in enumerate(rewritten_segments or []):
            item = dict(seg)
            if idx < len(source_segments):
                base = source_segments[idx]
                item["source_text"] = base.get("source_text") or base.get("text", "")
                item.setdefault("words", list(base.get("words", []) or []))
                item.setdefault("manual_highlights", list(base.get("manual_highlights", []) or []))
            normalized.append(item)
        single_line_enabled = bool(getattr(self.gui, "subtitle_single_line_cb", None) and self.gui.subtitle_single_line_cb.isChecked())
        if not single_line_enabled:
            return normalized
        orchestrator = TranslationOrchestrator()
        provider_type, polisher = orchestrator._resolve_ai_provider()
        if not polisher.is_configured():
            polisher = None
        return orchestrator._split_segments_for_single_line(
            normalized,
            polisher=polisher,
            provider_type=provider_type,
            target_lang=self.gui.get_target_language_code(),
        )

    def run_rewrite_translation(self):
        if hasattr(self.gui, "_translation_phase_complete") and not self.gui._translation_phase_complete():
            QMessageBox.information(self.gui, t("Rewrite Unavailable"), t("Complete the Translation phase before rewriting subtitles."))
            return
        source_segments = list(self.gui.current_segments or [])
        translated_segments = list(self.gui.current_translated_segments or [])
        if not source_segments:
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Original subtitles are missing. Please create or load the original subtitle track first."))
            return
        if not translated_segments:
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Vietnamese subtitles are missing. Please translate or load them first."))
            return
        rewrite_segments = self._collapse_translated_segments_for_rewrite(source_segments, translated_segments)
        if len(source_segments) != len(rewrite_segments):
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Could not rebuild the original subtitle groups for rewrite safely."))
            return
        self.gui._rewrite_source_segments = source_segments
        self.gui._rewrite_base_translated_segments = rewrite_segments
        self._open_rewrite_dialog(source_segments, rewrite_segments)

    def run_rewrite_selected_segment(self):
        if hasattr(self.gui, "_translation_phase_complete") and not self.gui._translation_phase_complete():
            QMessageBox.information(self.gui, t("Rewrite Unavailable"), t("Complete the Translation phase before rewriting subtitles."))
            return
        source_segments = list(self.gui.current_segments or [])
        translated_segments = list(self.gui.current_translated_segments or [])
        if not source_segments:
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Original subtitles are missing. Please create or load the original subtitle track first."))
            return
        if not translated_segments:
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Vietnamese subtitles are missing. Please translate or load them first."))
            return
        index = int(getattr(self.gui, "_selected_segment_index", -1))
        if not (0 <= index < len(translated_segments) and index < len(source_segments)):
            QMessageBox.warning(self.gui, t("Rewrite Unavailable"), t("Please select a subtitle block in the inspector first."))
            return

        # Kept as a compatibility entry point for old shortcuts/plugins. The
        # visible UI now uses one Rewrite button with a scope selector.
        self.gui._rewrite_initial_scope = "selected"
        self.gui._rewrite_initial_segment_index = index
        self._open_rewrite_dialog(source_segments, translated_segments, initial_scope="selected")

    def _validate_rewrite_srt(self, srt_text: str):
        normalized_text = str(srt_text or "").strip()
        expected_segments = getattr(self.gui, "_rewrite_base_translated_segments", None) or self.gui.current_translated_segments or self.gui.current_segments or []
        expected_len = len(expected_segments) or None
        is_valid, parsed_segments, validation_error = self.gui.validate_srt_text(normalized_text, expected_len=expected_len)
        if is_valid:
            return True, parsed_segments, "srt", ""
        return False, [], "invalid", validation_error or "Invalid SRT format."

    def on_rewrite_translation_finished(self, translated_srt, error):
        self.gui.rewrite_translation_btn.setEnabled(True)
        self.gui.rewrite_translation_btn.setText(t("Rewrite"))
        if hasattr(self.gui, "_rewrite_generate_btn"):
            self.gui._rewrite_generate_btn.setEnabled(True)
            self.gui._rewrite_generate_btn.setText(t("Generate Preview"))
        if hasattr(self.gui, "_rewrite_set_inputs_enabled"):
            self.gui._rewrite_set_inputs_enabled(True)
        if error or not translated_srt:
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.show_error(
                t("Rewrite Failed"),
                t("Could not rewrite the Vietnamese subtitles with AI."),
                error or t("The AI rewrite service returned an empty result."),
            )
            self.gui.refresh_ui_state()
            return

        if hasattr(self.gui, "_rewrite_preview_edit"):
            self.gui._rewrite_preview_ready = True
            self.gui._rewrite_preview_edit.setPlainText(translated_srt)
        if hasattr(self.gui, "_rewrite_preview_status_updater"):
            self.gui._rewrite_preview_status_updater()
        self.gui.update_project_step("refine_translation", "done")
        self.gui.refresh_ui_state()

    def on_rewrite_selected_segment_finished(self, translated_srt, error):
        def _cleanup_selected_rewrite_state():
            for attr in (
                "_rewrite_source_segments",
                "_rewrite_base_translated_segments",
                "_rewrite_selected_segment_index",
                "_rewrite_selected_segment_original_label",
            ):
                if hasattr(self.gui, attr):
                    delattr(self.gui, attr)

        original_label = str(getattr(self.gui, "_rewrite_selected_segment_original_label", "") or "Rewrite Selected Subtitle")
        if hasattr(self.gui, "rewrite_selected_segment_btn"):
            self.gui.rewrite_selected_segment_btn.setEnabled(True)
            self.gui.rewrite_selected_segment_btn.setText(original_label)

        index = int(getattr(self.gui, "_rewrite_selected_segment_index", -1))
        if error or not translated_srt:
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.show_error(
                t("Rewrite Failed"),
                t("Could not rewrite the selected subtitle block with AI."),
                error or t("The AI rewrite service returned an empty result."),
            )
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        if not (0 <= index < len(self.gui.current_translated_segments or [])):
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        is_valid_srt, parsed_segments, _mode, validation_error = self._validate_rewrite_srt(translated_srt)
        if not is_valid_srt or not parsed_segments:
            self.gui.show_error(
                t("Rewrite Failed"),
                t("The AI rewrite result for this block was not in valid SRT format."),
                validation_error or translated_srt,
            )
            self.gui.update_project_step("refine_translation", "failed")
            self.gui.refresh_ui_state()
            _cleanup_selected_rewrite_state()
            return

        rewritten_text = str(parsed_segments[0].get("text", "") or "").strip()
        target_segment = self.gui.current_translated_segments[index]
        target_segment["text"] = rewritten_text
        target_segment["tts_text"] = rewritten_text
        target_segment.setdefault("manual_highlights", [])
        self.gui._reconcile_manual_highlights(target_segment)
        self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
        self.gui._sync_hidden_translated_text_from_segments()
        self.gui.apply_segments_to_timeline()
        self.gui.persist_current_timeline_project_data()
        self.gui.refresh_auto_keyword_highlights(force=True)
        self.gui.schedule_live_subtitle_preview_refresh()
        self.gui.schedule_auto_frame_preview()
        self.gui.update_project_step("refine_translation", "done")
        self.gui.sync_segment_editor_rows()
        self.gui.refresh_ui_state()
        _cleanup_selected_rewrite_state()

    def apply_rewrite_preview(self):
        preview_edit = getattr(self.gui, "_rewrite_preview_edit", None)
        if not preview_edit:
            return
        translated_srt = preview_edit.toPlainText().strip()
        if not translated_srt:
            QMessageBox.warning(self.gui, t("Rewrite"), t("Please enter or generate rewritten subtitle content first."))
            return

        is_valid_srt, parsed_segments, _validation_mode, validation_error = self._validate_rewrite_srt(translated_srt)
        if not is_valid_srt:
            QMessageBox.warning(
                self.gui,
                t("Invalid SRT"),
                f"{t('Rewrite content must stay in valid SRT format.')}\n\n{validation_error}\n\n{t('Example:')}\n1\n00:00:01,000 --> 00:00:02,000\nXin chao",
            )
            return

        rewrite_source_segments = getattr(self.gui, "_rewrite_source_segments", None) or list(self.gui.current_segments or [])
        applied_segments = self._expand_rewrite_segments_for_current_layout(parsed_segments, rewrite_source_segments)

        selected_indices = [
            int(index)
            for index in list(getattr(self.gui, "_rewrite_selected_indices", []) or [])
            if 0 <= int(index) < len(self.gui.current_translated_segments or [])
        ]
        segment_index = int(getattr(self.gui, "_rewrite_selected_segment_index", -1))
        if selected_indices:
            if len(applied_segments) != len(selected_indices):
                QMessageBox.warning(
                    self.gui,
                    t("Rewrite"),
                    t("The AI returned a different number of cues than were checked. Nothing was changed."),
                )
                return
            for target_index, replacement in zip(selected_indices, applied_segments):
                self.gui.current_translated_segments[target_index] = replacement
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)
        elif segment_index >= 0 and segment_index < len(self.gui.current_translated_segments or []):
            self.gui.current_translated_segments[segment_index] = applied_segments[0] if applied_segments else dict(self.gui.current_translated_segments[segment_index])
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(self.gui.current_translated_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)
        else:
            self.gui.current_translated_segments = applied_segments
            self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(applied_segments, translated=True)
            normalized_srt = self.gui.format_to_srt(self.gui.current_translated_segments)

        self.gui.refresh_auto_keyword_highlights(force=True)
        self.gui.translated_text.setText(normalized_srt)
        self.gui.apply_segments_to_timeline()

        out_path = self.gui.last_translated_srt_path
        if not out_path:
            video_path = self.gui.video_path_edit.text().strip()
            if video_path:
                file_basename = os.path.splitext(os.path.basename(video_path))[0]
                out_folder = self.gui.get_project_temp_dir("subtitle")
                os.makedirs(out_folder, exist_ok=True)
                out_path = os.path.join(out_folder, file_basename + "_vi.srt")
        if out_path:
            with open(out_path, "w", encoding="utf-8") as handle:
                handle.write(normalized_srt)
            self.gui.last_translated_srt_path = out_path
            self.gui.processed_artifacts["srt_translated"] = out_path
            self.gui.persist_translation_project_data(self.gui.current_translated_segments, out_path)
        else:
            self.gui.persist_translation_project_data(self.gui.current_translated_segments)

        dialog = getattr(self.gui, "_rewrite_dialog", None)
        if dialog:
            dialog.accept()
        self.gui.schedule_live_subtitle_preview_refresh()
        self.gui.schedule_auto_frame_preview()
        self.gui.sync_segment_editor_rows()
        QMessageBox.information(self.gui, t("Rewrite Applied"), t("The rewritten SRT was applied to the subtitle editor."))
        self.gui.refresh_ui_state()

    def _open_rewrite_dialog(self, source_segments, translated_segments, *, initial_scope="all"):
        source_segments = list(source_segments or [])
        translated_segments = list(translated_segments or [])
        selected_index = int(getattr(self.gui, "_rewrite_initial_segment_index", -1))
        if not (0 <= selected_index < len(translated_segments)):
            selected_index = int(getattr(self.gui, "_selected_segment_index", -1))
        can_select_one = 0 <= selected_index < len(translated_segments) and selected_index < len(source_segments)
        dialog = QDialog(self.gui)
        dialog.setWindowTitle(t("Rewrite Subtitles"))
        dialog.setModal(True)
        dialog.setMinimumWidth(820)
        dialog.setMinimumHeight(680)
        dialog.setStyleSheet(
            """
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background: transparent; }
            QLabel#statusHeadline { color: #f8fbff; font-size: 16px; font-weight: 700; }
            QLabel#helperLabel { color: #9fb3ca; font-size: 12px; }
            QComboBox, QTextEdit {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                border-radius: 10px;
                padding: 8px 10px;
            }
            QComboBox QAbstractItemView {
                background-color: #132033;
                color: #f8fbff;
                border: 1px solid #2f4868;
                selection-background-color: #24486c;
            }
            QPushButton {
                background-color: #22344d;
                color: #f8fbff;
                border: 1px solid #34506f;
                border-radius: 10px;
                padding: 8px 16px;
                font-weight: 600;
            }
            QPushButton:hover { background-color: #29405d; }
            """
        )

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        title = QLabel("AI Rewrite")
        title.setObjectName("statusHeadline")
        layout.addWidget(title)

        hint = QLabel("Choose the rewrite scope and style, then ask AI for a revised subtitle. Review the read-only result and apply it when ready. For manual corrections, use Subtitle Editor.")
        hint.setObjectName("helperLabel")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scope_row = QHBoxLayout()
        scope_row.addWidget(QLabel("Scope:"))
        scope_combo = QComboBox(dialog)
        scope_combo.addItem("All translated subtitles", "all")
        scope_combo.addItem("Checked subtitles", "checked")
        if str(initial_scope or "all").strip().lower() == "selected" and can_select_one:
            scope_combo.setCurrentIndex(1)
        scope_row.addWidget(scope_combo, 1)
        scope_hint = QLabel("" if translated_segments else t("No translated subtitles"))
        scope_hint.setObjectName("helperLabel")
        scope_row.addWidget(scope_hint)
        layout.addLayout(scope_row)

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("Style:"))
        style_combo = QComboBox(dialog)
        for label, instruction in load_prompt_options(self.REWRITE_STYLE_PRESETS_FILE):
            style_combo.addItem(label, instruction)
        style_combo.addItem("Custom", "custom")
        style_row.addWidget(style_combo, 1)
        layout.addLayout(style_row)

        active_preset_id = (os.getenv("CAPCAP_TRANSLATION_PRESET_ID") or "general_default").strip()
        preset_info = get_preset_by_id(active_preset_id)
        preset_title = t(str(preset_info.get("name", active_preset_id) if preset_info else "General / Standard Subtitles"))
        inherited_hint = QLabel(t("🔗 Inheriting rules from: <b>{preset_title}</b>", preset_title=preset_title), dialog)
        inherited_hint.setObjectName("helperLabel")
        inherited_hint.setWordWrap(True)
        layout.addWidget(inherited_hint)

        custom_style_cb = QCheckBox("Add extra custom instruction", dialog)
        layout.addWidget(custom_style_cb)

        custom_prompt = QTextEdit(dialog)
        custom_prompt.setPlaceholderText("Example: Keep the words very short and modern, suitable for TikTok voiceover.")
        custom_prompt.setFixedHeight(88)
        custom_prompt.setVisible(False)
        layout.addWidget(custom_prompt)

        status_label = QLabel("Choose a scope and generate an AI rewrite preview.")
        status_label.setObjectName("helperLabel")
        status_label.setWordWrap(True)
        layout.addWidget(status_label)

        preview_label = QLabel("Translated subtitles")
        preview_label.setObjectName("sectionTitle")
        layout.addWidget(preview_label)

        preview_split = QVBoxLayout()
        preview_split.setSpacing(8)

        selection_actions = QHBoxLayout()
        select_all_btn = QPushButton("Select All", dialog)
        unselect_all_btn = QPushButton("Unselect All", dialog)
        selected_count_label = QLabel("0 / 0 selected")
        selected_count_label.setObjectName("helperLabel")
        selection_actions.addWidget(select_all_btn)
        selection_actions.addWidget(unselect_all_btn)
        selection_actions.addWidget(selected_count_label)
        selection_actions.addStretch()
        preview_split.addLayout(selection_actions)

        check_list = QListWidget(dialog)
        check_list.setMinimumHeight(180)
        check_list.setStyleSheet(
            "QListWidget { background: #132033; color: #eff6ff; border: 1px solid #2f4868; border-radius: 8px; padding: 3px; }"
            "QListWidget::item { padding: 7px 8px; }"
            "QListWidget::item:selected { background: #244d70; }"
        )
        for index, segment in enumerate(translated_segments):
            excerpt = " ".join(str(segment.get("text", "") or "").split())
            if len(excerpt) > 74:
                excerpt = excerpt[:71] + "..."
            start = float(segment.get("start", 0.0) or 0.0)
            end = float(segment.get("end", start) or start)
            start_ms = max(0, int(round(start * 1000)))
            end_ms = max(start_ms, int(round(end * 1000)))
            start_time = f"{start_ms // 3600000:02d}:{(start_ms // 60000) % 60:02d}:{(start_ms // 1000) % 60:02d},{start_ms % 1000:03d}"
            end_time = f"{end_ms // 3600000:02d}:{(end_ms // 60000) % 60:02d}:{(end_ms // 1000) % 60:02d},{end_ms % 1000:03d}"
            item = QListWidgetItem(f"{index + 1:03d}  {start_time} -> {end_time}\n{excerpt}", check_list)
            item.setData(Qt.UserRole, index)
            if str(initial_scope or "all").strip().lower() == "selected" and can_select_one:
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(Qt.Checked if index == selected_index else Qt.Unchecked)
            else:
                item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
        preview_split.addWidget(check_list, 1)

        preview_edit = QTextEdit(dialog)
        preview_edit.setPlaceholderText("The AI rewrite preview will appear here.")
        preview_edit.setReadOnly(True)
        preview_edit.setMinimumHeight(180)
        preview_edit.setVisible(False)
        preview_split.addWidget(preview_edit, 2)
        layout.addLayout(preview_split, 1)

        self.gui._rewrite_dialog = dialog
        self.gui._rewrite_preview_edit = preview_edit
        self.gui._rewrite_preview_ready = False
        self.gui._rewrite_selected_segment_index = -1
        self.gui._rewrite_selected_indices = []

        button_row = QHBoxLayout()
        button_row.addStretch()
        generate_btn = QPushButton("Generate Preview", dialog)
        apply_btn = QPushButton("Apply Rewrite", dialog)
        button_row.addWidget(generate_btn)
        button_row.addWidget(apply_btn)
        layout.addLayout(button_row)

        self.gui._rewrite_apply_btn = apply_btn
        self.gui._rewrite_generate_btn = generate_btn
        self.gui._rewrite_status_label = status_label

        def _toggle_custom_instruction(checked: bool):
            custom_prompt.setVisible(bool(checked))
            _invalidate_preview(t("Custom instruction changed. Generate a new preview."))

        def _build_style_instruction() -> str:
            base_instruction = str(style_combo.currentData() or "").strip()
            if base_instruction == "custom":
                base_instruction = ""
            extra_instruction = custom_prompt.toPlainText().strip() if custom_style_cb.isChecked() else ""
            return " ".join(part for part in [base_instruction, extra_instruction] if part).strip()

        def _scope_segments():
            if scope_combo.currentData() == "checked":
                indices = [
                    int(item.data(Qt.UserRole))
                    for row in range(check_list.count())
                    for item in [check_list.item(row)]
                    if item.checkState() == Qt.Checked
                ]
                indices = [index for index in indices if 0 <= index < len(source_segments) and index < len(translated_segments)]
                return [source_segments[index] for index in indices], [translated_segments[index] for index in indices]
            return source_segments, translated_segments

        def _scope_indices():
            if scope_combo.currentData() != "checked":
                return []
            return [
                int(item.data(Qt.UserRole))
                for row in range(check_list.count())
                for item in [check_list.item(row)]
                if item.checkState() == Qt.Checked
                and 0 <= int(item.data(Qt.UserRole)) < len(translated_segments)
            ]

        def _scope_view():
            return scope_combo.currentData() == "checked"

        def _update_selection_summary():
            checked_scope = _scope_view()
            selected_indices = _scope_indices()
            if checked_scope:
                selected_count_label.setText(
                    t("{selected} / {total} selected", selected=len(selected_indices), total=len(translated_segments))
                )
                scope_hint.setText(
                    t("Select the subtitles to rewrite.")
                    if not selected_indices
                    else t("{count} subtitle(s) selected", count=len(selected_indices))
                )
            else:
                selected_count_label.setText(t("{count} subtitle(s)", count=len(translated_segments)))
                scope_hint.setText(
                    t("All {count} translated subtitles will be rewritten.", count=len(translated_segments))
                    if translated_segments
                    else t("No translated subtitles")
                )
            select_all_btn.setVisible(checked_scope)
            unselect_all_btn.setVisible(checked_scope)
            select_all_btn.setEnabled(checked_scope and check_list.count() > 0)
            unselect_all_btn.setEnabled(checked_scope and bool(selected_indices))

        def _show_selection_view():
            preview_label.setText(t("Select subtitles to rewrite") if _scope_view() else t("All translated subtitles"))
            check_list.setVisible(True)
            select_all_btn.setVisible(_scope_view())
            unselect_all_btn.setVisible(_scope_view())
            selected_count_label.setVisible(True)
            preview_edit.setVisible(False)

        def _show_result_view():
            preview_label.setText(t("AI Rewrite Preview"))
            check_list.setVisible(False)
            select_all_btn.setVisible(False)
            unselect_all_btn.setVisible(False)
            selected_count_label.setVisible(False)
            preview_edit.setVisible(True)

        def _set_inputs_enabled(enabled: bool):
            scope_combo.setEnabled(enabled)
            style_combo.setEnabled(enabled)
            custom_style_cb.setEnabled(enabled)
            custom_prompt.setEnabled(enabled)
            check_list.setEnabled(enabled)
            select_all_btn.setEnabled(enabled and _scope_view() and check_list.count() > 0)
            unselect_all_btn.setEnabled(enabled and _scope_view() and bool(_scope_indices()))

        def _invalidate_preview(message="Choose a scope and generate an AI rewrite preview."):
            _source, _translated = _scope_segments()
            selected_indices = _scope_indices()
            self.gui._rewrite_selected_indices = selected_indices
            self.gui._rewrite_selected_segment_index = selected_indices[0] if len(selected_indices) == 1 else -1
            self.gui._rewrite_source_segments = _source
            self.gui._rewrite_base_translated_segments = _translated
            self.gui._rewrite_preview_ready = False
            preview_edit.clear()
            _show_selection_view()
            _update_selection_summary()
            if _scope_view() and not selected_indices:
                status_label.setText(t("Check at least one subtitle before generating an AI rewrite."))
            else:
                status_label.setText(t(message))
            status_label.setStyleSheet("")
            apply_btn.setEnabled(False)

        def _reset_scope_preview():
            _invalidate_preview()

        def _update_preview_validity():
            if getattr(self.gui, "_rewrite_preview_ready", False):
                _show_result_view()
            else:
                _show_selection_view()
            current_text = preview_edit.toPlainText().strip()
            if not current_text:
                if getattr(self.gui, "_rewrite_preview_ready", False):
                    status_label.setText(t("The AI rewrite returned an empty result."))
                apply_btn.setEnabled(False)
                return
            is_valid_srt, parsed_segments, validation_mode, validation_error = self._validate_rewrite_srt(current_text)
            if is_valid_srt and validation_mode == "srt" and getattr(self.gui, "_rewrite_preview_ready", False):
                status_label.setText(
                    t("AI rewrite ready. Review the read-only result ({count} subtitle(s)), then apply it.", count=len(parsed_segments))
                )
                status_label.setStyleSheet("color: #78f0b0; font-size: 12px; font-weight: 700;")
                apply_btn.setEnabled(True)
            elif not getattr(self.gui, "_rewrite_preview_ready", False):
                apply_btn.setEnabled(False)
            else:
                status_label.setText(
                    f"{t('Invalid SRT.')} {validation_error or t('Keep standard blocks: index, time range, then subtitle text.')}"
                )
                status_label.setStyleSheet("color: #ff8f8f; font-size: 12px; font-weight: 700;")
                apply_btn.setEnabled(False)

        def _start_preview_generation():
            rewrite_source_segments, rewrite_base_segments = _scope_segments()
            if not rewrite_base_segments:
                QMessageBox.information(dialog, t("Rewrite"), t("Check at least one subtitle before generating an AI rewrite."))
                return
            style_instruction = _build_style_instruction()
            self.gui._rewrite_preview_ready = False
            preview_edit.clear()
            _show_selection_view()
            status_label.setText(t("Generating rewrite preview with AI..."))
            apply_btn.setEnabled(False)
            generate_btn.setEnabled(False)
            generate_btn.setText(t("Generating..."))
            _set_inputs_enabled(False)
            self.gui.rewrite_translation_btn.setEnabled(False)
            self.gui.rewrite_translation_btn.setText(t("Rewriting..."))
            self.gui.progress_bar.setValue(90)
            self.gui.update_project_step("refine_translation", "running")

            self.gui._rewrite_preview_status_updater = _update_preview_validity
            self.gui._rewrite_set_inputs_enabled = _set_inputs_enabled

            self.gui._rewrite_source_segments = rewrite_source_segments
            self.gui._rewrite_base_translated_segments = rewrite_base_segments
            self.gui._rewrite_selected_indices = _scope_indices()
            self.gui._rewrite_selected_segment_index = self.gui._rewrite_selected_indices[0] if len(self.gui._rewrite_selected_indices) == 1 else -1
            self.gui.rewrite_translation_thread = RewriteTranslationWorker(
                rewrite_source_segments,
                rewrite_base_segments,
                self.gui.get_source_language_code(),
                style_instruction=style_instruction,
            )
            self.gui.rewrite_translation_thread.finished.connect(self.gui.on_rewrite_translation_finished)
            self.gui.rewrite_translation_thread.progress.connect(self.on_translation_progress)
            self.gui.rewrite_translation_thread.batch_ready.connect(self.on_translation_batch_ready)
            self.gui.rewrite_translation_thread.start()

        def _cleanup_dialog():
            for attr in (
                "_rewrite_dialog",
                "_rewrite_preview_edit",
                "_rewrite_apply_btn",
                "_rewrite_generate_btn",
                "_rewrite_status_label",
                "_rewrite_preview_status_updater",
                "_rewrite_set_inputs_enabled",
                "_rewrite_source_segments",
                "_rewrite_base_translated_segments",
                "_rewrite_preview_ready",
                "_rewrite_selected_segment_index",
                "_rewrite_selected_indices",
                "_rewrite_initial_scope",
                "_rewrite_initial_segment_index",
            ):
                if hasattr(self.gui, attr):
                    delattr(self.gui, attr)

        def _set_checkability(checked_scope: bool):
            check_list.blockSignals(True)
            try:
                for row in range(check_list.count()):
                    item = check_list.item(row)
                    if checked_scope:
                        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                        if item.checkState() not in (Qt.Checked, Qt.Unchecked):
                            item.setCheckState(Qt.Unchecked)
                    else:
                        item.setCheckState(Qt.Unchecked)
                        item.setFlags(item.flags() & ~Qt.ItemIsUserCheckable)
            finally:
                check_list.blockSignals(False)

        def _on_scope_changed(_index):
            _set_checkability(_scope_view())
            _reset_scope_preview()

        def _set_all_checks(checked: bool):
            if not _scope_view():
                return
            check_list.blockSignals(True)
            try:
                for row in range(check_list.count()):
                    check_list.item(row).setCheckState(Qt.Checked if checked else Qt.Unchecked)
            finally:
                check_list.blockSignals(False)
            _invalidate_preview()

        custom_style_cb.toggled.connect(_toggle_custom_instruction)
        scope_combo.currentIndexChanged.connect(_on_scope_changed)
        style_combo.currentIndexChanged.connect(lambda _index: _invalidate_preview(t("Rewrite style changed. Generate a new preview.")))
        custom_prompt.textChanged.connect(
            lambda _text: _invalidate_preview(t("Custom instruction changed. Generate a new preview."))
        )

        def _handle_scope_item_changed(item):
            if _scope_view():
                _invalidate_preview()

        check_list.itemChanged.connect(_handle_scope_item_changed)
        select_all_btn.clicked.connect(lambda: _set_all_checks(True))
        unselect_all_btn.clicked.connect(lambda: _set_all_checks(False))
        generate_btn.clicked.connect(_start_preview_generation)
        apply_btn.clicked.connect(self.apply_rewrite_preview)
        dialog.finished.connect(lambda _result: _cleanup_dialog())
        self.gui._rewrite_preview_status_updater = _update_preview_validity
        self.gui._rewrite_set_inputs_enabled = _set_inputs_enabled
        _set_checkability(_scope_view())
        _reset_scope_preview()
        _update_preview_validity()
        dialog.exec()

    def apply_edited_translation(self, show_message=True, force_apply=True):
        srt_text = self.gui.translated_text.toPlainText()
        segments = []
        if self.gui.keep_timeline_cb.isChecked():
            base_segments = self.gui.current_translated_segments or self.gui.current_segments
            edited_texts = self.gui.extract_subtitle_text_entries(srt_text)
            if base_segments and len(edited_texts) == len(base_segments):
                segments = []
                for idx, base in enumerate(base_segments):
                    d = {
                        "start": base["start"],
                        "end": base["end"],
                        "text": edited_texts[idx],
                        "words": list(base.get("words", [])),
                        "manual_highlights": list(base.get("manual_highlights", [])),
                        "speaker": str(base.get("speaker", "") or ""),
                    }
                    for fld in (
                        "tts_text", "tts_group_id", "tts_group_start", "tts_group_end",
                        "extended_duration", "time_warp_id", "_audio_end", "_wav_path",
                        "provider", "translation_provider",
                    ):
                        if fld in base:
                            d[fld] = base[fld]
                    segments.append(d)
        if not segments:
            segments = self.gui.parse_srt_to_segments(srt_text)
        # Imported/edited SRT files cannot carry diarization metadata.  When
        # cue order is unchanged, restore the speaker assignment from the
        # existing project regardless of the timeline-preserve preference.
        # This keeps speaker colors and per-speaker voice routing intact.
        if segments:
            metadata_base = self.gui.current_translated_segments or self.gui.current_segments
            if metadata_base and len(metadata_base) == len(segments):
                for idx, segment in enumerate(segments):
                    speaker = str(metadata_base[idx].get("speaker", "") or "").strip()
                    if speaker:
                        segment["speaker"] = speaker
                    for fld in (
                        "tts_text", "tts_group_id", "tts_group_start", "tts_group_end",
                        "extended_duration", "time_warp_id", "_audio_end", "_wav_path",
                        "provider", "translation_provider",
                    ):
                        if fld in metadata_base[idx] and fld not in segment:
                            segment[fld] = metadata_base[idx][fld]
        if not segments:
            if show_message:
                QMessageBox.warning(
                    self.gui,
                    t("Error"),
                    t("Could not parse edited translated SRT.\n\nTip: Keep standard SRT format:\n1\\n00:00:01,000 --> 00:00:02,000\\ntext"),
                )
            return False

        default_provider = getattr(self.gui, "_last_translation_provider", "") or self.gui._completed_translation_provider_label() or "google"
        for segment in segments:
            if not segment.get("provider") and not segment.get("translation_provider"):
                segment["provider"] = default_provider

        self.gui.current_translated_segments = segments
        self.gui.current_translated_segment_models = self.gui._dict_segments_to_models(segments, translated=True)
        if force_apply:
            self.gui.apply_segments_to_timeline()

        if show_message:
            QMessageBox.information(
                self.gui,
                t("Applied"),
                t("Applied edited translation to timeline.\nSegments: {count}", count=len(segments)),
            )
        self.gui.refresh_ui_state()
        self.gui.schedule_auto_frame_preview()
        return True
