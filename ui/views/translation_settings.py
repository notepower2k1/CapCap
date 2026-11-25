import os
from dataclasses import dataclass

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from ui.i18n import t


@dataclass(frozen=True)
class TranslationSettingsFields:
    provider: QComboBox
    api_key: QLineEdit
    model: QLineEdit
    base_url: QLineEdit
    preset: QComboBox
    auto_context: QCheckBox
    browser_platform: QComboBox
    browser_temp_chat: QCheckBox
    browser_workers: QComboBox


def build_translation_settings(dialog, content, layout, settings, log) -> TranslationSettingsFields:
    try:
        from translation.providers.browser_bridge_provider import (
            ensure_bridge_server_running,
            get_bridge_status,
            request_open_web_tab,
        )
        ensure_bridge_server_running()
    except Exception:
        get_bridge_status = lambda: {"ok": False, "extension_connected": False}
        request_open_web_tab = lambda platform="gemini", temporary_chat=True: {"ok": False}

    provider_layout = QHBoxLayout()
    provider_label = QLabel(t("Translator Provider:"), content)
    provider_layout.addWidget(provider_label)
    provider_combo = QComboBox(content)
    provider_combo.addItem("Google Translate (free, no key)", "google")
    provider_combo.addItem("Bing Translator (free, no key)", "bing")
    provider_combo.addItem(t("Direct Browser AI (Gemini / ChatGPT Web)"), "browser_ai")
    provider_combo.addItem("Google AI Studio", "google_ai_studio")
    provider_combo.addItem("OpenAI", "openai")
    provider_combo.addItem("Ollama (Local)", "ollama")
    current_provider = (os.getenv("OPENAI_PROVIDER") or os.getenv("AI_POLISHER_PROVIDER") or settings.value("translation_provider", "google")).strip().lower()
    if current_provider == "gemini":
        current_provider = "google_ai_studio"
    elif current_provider in ("gemini_web", "chatgpt_web"):
        current_provider = "browser_ai"
    if current_provider not in {"google", "bing", "google_ai_studio", "openai", "ollama", "browser_ai"}:
        current_provider = "google"
    idx = provider_combo.findData(current_provider)
    if idx >= 0:
        provider_combo.setCurrentIndex(idx)
    provider_layout.addWidget(provider_combo, 1)
    layout.addLayout(provider_layout)

    def _provider_values(provider):
        if provider == "google_ai_studio":
            legacy = str(os.getenv("OPENAI_PROVIDER") or "").strip().lower() == "gemini"
            return (
                os.getenv("GOOGLE_AI_STUDIO_API_KEY", "") or (os.getenv("OPENAI_API_KEY", "") if legacy else "") or str(settings.value("google_ai_studio_api_key", "") or ""),
                os.getenv("GOOGLE_AI_STUDIO_MODEL", "") or (os.getenv("OPENAI_MODEL", "") if legacy else "") or str(settings.value("google_ai_studio_model", "") or ""),
                os.getenv("GOOGLE_AI_STUDIO_BASE_URL", "") or (os.getenv("OPENAI_BASE_URL", "") if legacy else "") or str(settings.value("google_ai_studio_base_url", "") or ""),
            )
        elif provider == "ollama":
            return (
                "",
                os.getenv("OLLAMA_MODEL", "") or os.getenv("OPENAI_MODEL", "") or str(settings.value("ollama_model", "") or "gemma4:31b-cloud"),
                os.getenv("OLLAMA_BASE_URL", "") or os.getenv("OPENAI_BASE_URL", "") or str(settings.value("ollama_base_url", "") or "http://localhost:11434/v1"),
            )
        return (
            os.getenv("OPENAI_API_KEY", "") or str(settings.value("openai_api_key", "") or ""),
            os.getenv("OPENAI_MODEL", "") or str(settings.value("openai_model", "") or ""),
            os.getenv("OPENAI_BASE_URL", "") or str(settings.value("openai_base_url", "") or ""),
        )

    initial_key, initial_model, initial_base_url = _provider_values(current_provider)

    key_section_widget = QWidget(content)
    key_layout = QVBoxLayout(key_section_widget)
    key_layout.setContentsMargins(0, 0, 0, 0)
    key_label = QLabel(t("API Key:"), key_section_widget)
    key_edit = QLineEdit(key_section_widget)
    key_edit.setEchoMode(QLineEdit.Password)
    key_edit.setText(initial_key)
    key_layout.addWidget(key_label)
    key_layout.addWidget(key_edit)
    layout.addWidget(key_section_widget)

    model_layout = QVBoxLayout()
    model_label = QLabel(t("AI Model:"), content)
    model_edit = QLineEdit(content)
    model_edit.setText(initial_model)
    model_layout.addWidget(model_label)
    model_layout.addWidget(model_edit)
    layout.addLayout(model_layout)

    base_url_layout = QVBoxLayout()
    base_url_label = QLabel("API URL:", content)
    base_url_edit = QLineEdit(content)
    base_url_edit.setText(initial_base_url)
    base_url_layout.addWidget(base_url_label)
    base_url_layout.addWidget(base_url_edit)
    layout.addLayout(base_url_layout)

    browser_ai_widget = QWidget(content)
    browser_ai_layout = QVBoxLayout(browser_ai_widget)
    browser_ai_layout.setContentsMargins(0, 0, 0, 0)
    browser_ai_layout.setSpacing(8)

    browser_platform_layout = QHBoxLayout()
    browser_platform_label = QLabel(t("Web AI Platform:"), browser_ai_widget)
    browser_platform_combo = QComboBox(browser_ai_widget)
    browser_platform_combo.addItem("Google Gemini Web (gemini.google.com)", "gemini")
    browser_platform_combo.addItem("ChatGPT Web (chatgpt.com)", "chatgpt")
    initial_browser_plat = str(os.getenv("CAPCAP_BROWSER_AI_PLATFORM") or settings.value("browser_ai_platform", "gemini") or "gemini").strip().lower()
    idx_bp = browser_platform_combo.findData(initial_browser_plat)
    if idx_bp >= 0:
        browser_platform_combo.setCurrentIndex(idx_bp)
    browser_platform_layout.addWidget(browser_platform_label)
    browser_platform_layout.addWidget(browser_platform_combo, 1)
    browser_ai_layout.addLayout(browser_platform_layout)

    browser_temp_chat_cb = QCheckBox(t("Temporary chat / Do not save chat history (prevents chat clutter)"), browser_ai_widget)
    initial_temp_chat = str(os.getenv("CAPCAP_BROWSER_AI_TEMP_CHAT", settings.value("browser_ai_temp_chat", "1"))).strip().lower() not in ("0", "false", "no")
    browser_temp_chat_cb.setChecked(initial_temp_chat)
    browser_temp_chat_cb.setToolTip(t("Uses Temporary Chat on ChatGPT and verifies Temporary Chat is enabled on Gemini before sending. If unavailable, translation stops with an error."))
    browser_ai_layout.addWidget(browser_temp_chat_cb)

    browser_workers_row = QHBoxLayout()
    browser_workers_row.addWidget(QLabel(t("Concurrent batches:"), browser_ai_widget))
    browser_workers_combo = QComboBox(browser_ai_widget)
    browser_workers_combo.setObjectName("browserAIWorkersComboBox")
    browser_workers_combo.addItem(t("1 — Sequential"), "1")
    for browser_worker_count in (2, 3, 4):
        browser_workers_combo.addItem(t("{count} — Parallel, separate chats", count=browser_worker_count), str(browser_worker_count))
    browser_workers_combo.setToolTip(t("Each parallel batch uses a separate tab or window. Keep Gemini windows visible and uncovered. Speed depends on your AI account limits."))
    initial_workers = str(os.getenv("CAPCAP_BROWSER_AI_WORKERS") or settings.value("browser_ai_workers", "1"))
    browser_workers_combo.setCurrentIndex(max(0, browser_workers_combo.findData(initial_workers)))
    browser_workers_row.addWidget(browser_workers_combo, 1)
    browser_ai_layout.addLayout(browser_workers_row)
    browser_workers_hint = QLabel(t("While translating, keep GPT/Gemini windows visible and large enough to show the chat controls. Do not minimize them to the taskbar or cover them completely; this may delay sending or reading replies."), browser_ai_widget)
    browser_workers_hint.setWordWrap(True)
    browser_ai_layout.addWidget(browser_workers_hint)

    ext_status_label = QLabel(browser_ai_widget)
    ext_status_label.setWordWrap(True)
    browser_ai_layout.addWidget(ext_status_label)

    extension_update_note = t(
        "After updating CapCap, click Reload for the CapCap extension at chrome://extensions or edge://extensions, then refresh open GPT/Gemini tabs. "
        "If the extension is in a different folder, update that copy or use Load unpacked with the new folder first."
    )

    def refresh_extension_status():
        st = get_bridge_status()
        if st.get("extension_connected"):
            port = st.get("port", 18765)
            ext_status_label.setText(t("🟢 Browser Extension Connected (Port {port}) — Synchronized with CapCap", port=port))
            ext_status_label.setStyleSheet("color: #34d399; font-weight: 500;")
        else:
            ext_status_label.setText(t("⚪ Browser Extension Not Connected — Click 'Quick Install Extension' below"))
            ext_status_label.setStyleSheet("color: #fbbf24; font-weight: 500;")

    def _on_browser_settings_changed():
        plat = browser_platform_combo.currentData() or "gemini"
        temp_chat = browser_temp_chat_cb.isChecked()
        os.environ["CAPCAP_BROWSER_AI_PLATFORM"] = plat
        os.environ["CAPCAP_BROWSER_AI_TEMP_CHAT"] = "1" if temp_chat else "0"
        os.environ["CAPCAP_BROWSER_AI_WORKERS"] = browser_workers_combo.currentData() or "1"

    browser_platform_combo.currentIndexChanged.connect(_on_browser_settings_changed)
    browser_temp_chat_cb.toggled.connect(_on_browser_settings_changed)
    browser_workers_combo.currentIndexChanged.connect(_on_browser_settings_changed)

    def _install_extension():
        from app.utils.browser_extension_utils import get_browser_extension_dir
        try:
            ext_dir = get_browser_extension_dir()
        except FileNotFoundError as exc:
            QMessageBox.warning(dialog, t("Quick Install Extension"), str(exc))
            return
        QApplication.clipboard().setText(ext_dir)
        try:
            os.startfile(ext_dir)
        except Exception:
            pass
        QMessageBox.information(
            dialog,
            t("Quick Install Browser Extension (1-Time Setup)"),
            t(
                "1. Open Chrome or Edge and navigate to chrome://extensions (or edge://extensions).\n"
                "2. Turn ON Developer mode in the top right.\n"
                "3. Click 'Load unpacked' and paste (Ctrl+V) the folder path already copied to your clipboard (or drag and drop the opened 'browser_extension' folder into the browser)."
            ) + "\n\n" + extension_update_note,
        )

    def _open_web_tab():
        plat = browser_platform_combo.currentData() or "gemini"
        temp_chat = browser_temp_chat_cb.isChecked()
        os.environ["CAPCAP_BROWSER_AI_PLATFORM"] = plat
        os.environ["CAPCAP_BROWSER_AI_TEMP_CHAT"] = "1" if temp_chat else "0"
        request_open_web_tab(platform=plat, temporary_chat=temp_chat)

    ext_btn_row = QHBoxLayout()
    install_ext_btn = QPushButton(t("Quick Install Extension"), browser_ai_widget)
    install_ext_btn.clicked.connect(_install_extension)
    open_web_btn = QPushButton(t("Open Web AI Tab"), browser_ai_widget)
    open_web_btn.clicked.connect(_open_web_tab)
    ext_btn_row.addWidget(install_ext_btn)
    ext_btn_row.addWidget(open_web_btn)
    browser_ai_layout.addLayout(ext_btn_row)

    extension_update_hint = QLabel(extension_update_note, browser_ai_widget)
    extension_update_hint.setWordWrap(True)
    extension_update_hint.setTextInteractionFlags(Qt.TextSelectableByMouse)
    browser_ai_layout.addWidget(extension_update_hint)

    ext_status_timer = QTimer(dialog)
    def _poll_ext_status():
        if provider_combo.currentData() == "browser_ai":
            refresh_extension_status()
    ext_status_timer.timeout.connect(_poll_ext_status)
    ext_status_timer.start(1500)
    dialog.finished.connect(ext_status_timer.stop)

    layout.addWidget(browser_ai_widget)

    # Translation Prompt Preset Selector
    preset_layout = QVBoxLayout()
    preset_layout.setSpacing(4)
    preset_label = QLabel(t("Default Prompt Preset:"), content)
    translation_preset_combo = QComboBox(content)

    try:
        from translation import load_translation_presets
        available_presets = load_translation_presets()
    except Exception:
        available_presets = []

    for p in available_presets:
        p_id = p.get("id", "")
        p_name = p.get("name", p_id)
        translation_preset_combo.addItem(p_name, p_id)

    initial_preset_id = str(
        settings.value("translation_preset_id", os.getenv("CAPCAP_TRANSLATION_PRESET_ID", "general_default"))
        or os.getenv("CAPCAP_TRANSLATION_PRESET_ID", "general_default")
    ).strip()
    idx_preset = translation_preset_combo.findData(initial_preset_id)
    if idx_preset >= 0:
        translation_preset_combo.setCurrentIndex(idx_preset)
    elif translation_preset_combo.count() > 0:
        translation_preset_combo.setCurrentIndex(0)

    preset_desc_label = QLabel("", content)
    preset_desc_label.setObjectName("helperLabel")
    preset_desc_label.setWordWrap(True)

    def _update_preset_desc():
        curr_id = translation_preset_combo.currentData()
        for p in available_presets:
            if p.get("id") == curr_id:
                preset_desc_label.setText(t(p.get("description", "")))
                break
        else:
            preset_desc_label.setText("")

    translation_preset_combo.currentIndexChanged.connect(_update_preset_desc)
    _update_preset_desc()

    preset_layout.addWidget(preset_label)
    preset_layout.addWidget(translation_preset_combo)
    preset_layout.addWidget(preset_desc_label)
    layout.addLayout(preset_layout)

    auto_context_cb = QCheckBox(t("Auto-detect dialogue context & character pronouns"), content)
    initial_auto_context = str(
        settings.value("auto_translation_context", os.getenv("CAPCAP_AUTO_TRANSLATION_CONTEXT", "1"))
        or os.getenv("CAPCAP_AUTO_TRANSLATION_CONTEXT", "1")
    ).strip().lower() not in ("0", "false", "no")
    auto_context_cb.setChecked(initial_auto_context)
    auto_context_cb.setToolTip(
        "Analyzes dialogue context and speaker relationships across segments to build character profiles "
        "and bidirectional pronoun addressing rules (address_rules) for consistent translation."
    )
    layout.addWidget(auto_context_cb)

    provider_hint = QLabel("Get an API key at https://aistudio.google.com/apikey", content)
    provider_hint.setObjectName("helperLabel")
    provider_hint.setWordWrap(True)
    layout.addWidget(provider_hint)

    test_btn = QPushButton(t("Test Connection"), content)
    test_status = QLabel("", content)
    test_status.setObjectName("helperLabel")
    test_row = QHBoxLayout()
    test_row.addWidget(test_btn)
    test_row.addWidget(test_status, 1)
    layout.addLayout(test_row)

    def test_ai_connection():
        url = base_url_edit.text().strip()
        provider = provider_combo.currentData()
        key = key_edit.text().strip() or ("ollama" if provider == "ollama" else "")
        model = model_edit.text().strip()
        if not url:
            test_status.setText(t("Enter a server URL first."))
            return
        if not key:
            test_status.setText(t("Enter an API key first."))
            return
        if not model:
            test_status.setText(t("Enter a model name first."))
            return
        test_status.setText(t("Testing..."))
        test_status.repaint()
        try:
            from openai import OpenAI
            client = OpenAI(api_key=key, base_url=url, timeout=15.0)
            client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with OK."}],
                max_tokens=8,
            )
            test_status.setText(t("Connected: {model}", model=model))
        except Exception as e:
            if provider == "ollama":
                log(f"[Ollama] Connection test failed: {e}")
                test_status.setText(t("Unable to connect to Ollama. Please check your connection and settings."))
            else:
                test_status.setText(t("Failed: {error}", error=e))

    test_btn.clicked.connect(test_ai_connection)

    def update_provider_fields():
        p = provider_combo.currentData()
        model_edit.setPlaceholderText("")
        is_browser_ai = p == "browser_ai"
        is_ai = p not in ("google", "bing")
        is_google_ai_studio = p == "google_ai_studio"
        is_openai = p == "openai"
        key_section_widget.setVisible(is_google_ai_studio or is_openai)
        base_url_label.setVisible(is_ai and not is_browser_ai)
        base_url_edit.setVisible(is_ai and not is_browser_ai)
        preset_label.setVisible(is_ai)
        translation_preset_combo.setVisible(is_ai)
        preset_desc_label.setVisible(is_ai)
        auto_context_cb.setVisible(is_ai)
        test_btn.setVisible(is_ai and not is_browser_ai)
        test_status.setVisible(is_ai and not is_browser_ai)
        model_label.setVisible(is_ai and not is_browser_ai)
        model_edit.setVisible(is_ai and not is_browser_ai)
        browser_ai_widget.setVisible(is_browser_ai)
        if p == "google":
            provider_hint.setText(t("Free Google web translate, no API key needed. Lower quality than AI translation."))
            key_edit.clear()
            model_edit.clear()
            base_url_edit.clear()
        elif p == "bing":
            provider_hint.setText(t("Free Bing web translate, no API key needed. Lower quality than AI translation."))
            key_edit.clear()
            model_edit.clear()
            base_url_edit.clear()
        elif is_browser_ai:
            provider_hint.setText(t("Translates directly using your logged-in Google Gemini or ChatGPT browser tab via the CapCap Extension. Free, no API key needed."))
            refresh_extension_status()
        elif is_google_ai_studio:
            model_label.setText(t("AI Model:"))
            key, model, base_url = _provider_values(p)
            key_edit.setText(key)
            model_edit.setText(model)
            base_url_edit.setText(base_url or "https://generativelanguage.googleapis.com/v1beta/openai/")
            if not model_edit.text().strip():
                model_edit.setText("gemini-3.7-flash")
            provider_hint.setText(t("Use a Google AI Studio Gemini API key: https://aistudio.google.com/apikey"))
        elif is_openai:
            model_label.setText(t("AI Model:"))
            key, model, base_url = _provider_values(p)
            key_edit.setText(key)
            model_edit.setText(model)
            base_url_edit.setText(base_url or "https://api.openai.com/v1/")
            if not model_edit.text().strip():
                model_edit.setText("gpt-4o-mini")
            provider_hint.setText(t("Get an API key at https://platform.openai.com/api-keys"))
        elif p == "ollama":
            model_label.setText(t("AI Model:"))
            key, model, base_url = _provider_values(p)
            base_url_edit.setText(base_url or "http://localhost:11434/v1")
            key_edit.clear()
            model_edit.setText(model or "gemma4:31b-cloud")
            provider_hint.setText(t("Requires a running Ollama server. Speed depends on your hardware; tasks will fail/fallback if timeout expires."))
        model_edit.setReadOnly(False)

    provider_combo.currentIndexChanged.connect(update_provider_fields)
    update_provider_fields()

    layout.addStretch(1)
    return TranslationSettingsFields(
        provider=provider_combo,
        api_key=key_edit,
        model=model_edit,
        base_url=base_url_edit,
        preset=translation_preset_combo,
        auto_context=auto_context_cb,
        browser_platform=browser_platform_combo,
        browser_temp_chat=browser_temp_chat_cb,
        browser_workers=browser_workers_combo,
    )
