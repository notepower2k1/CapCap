from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QWidget

from ui.i18n import t


def _add_dictionary_row(table):
    from PySide6.QtWidgets import QTableWidgetItem
    r = table.rowCount()
    table.insertRow(r)
    table.setItem(r, 0, QTableWidgetItem(""))
    table.setItem(r, 1, QTableWidgetItem(""))
    table.scrollToBottom()


def _remove_dictionary_rows(table):
    rows = sorted({idx.row() for idx in table.selectedIndexes()}, reverse=True)
    for r in rows:
        table.removeRow(r)


def show_normalizer_dict_dialog(gui):
    """Edit the pronunciation dictionary stored in this project.

        This is deliberately project state rather than a global CSV resource:
        the same word may need different pronunciation in different videos.
        The dictionary is passed to Piper only; subtitle text is untouched.
        """
    if not str(gui.video_path_edit.text().strip() if hasattr(gui, "video_path_edit") else ""):
        QMessageBox.information(gui, t("Normalizer Dictionary"), t("Open a project before editing its pronunciation dictionary."))
        return
    state = gui.ensure_current_project()
    if state is None:
        QMessageBox.information(gui, t("Normalizer Dictionary"), t("Open a project before editing its pronunciation dictionary."))
        return
    if not gui._translation_phase_complete():
        QMessageBox.information(gui, t("Normalizer Dictionary"), t("Complete the Translation phase before editing the project dictionary."))
        return

    DICT_DEFS = [
        {"label": "Acronyms", "key": "acronyms", "col_a": "acronym", "col_b": "transliteration"},
        {"label": "Non-Vietnamese Words", "key": "non_vietnamese_words", "col_a": "original", "col_b": "transliteration"},
    ]

    dialog = QDialog(gui)
    if hasattr(gui, "light_window_icon") and gui.light_window_icon and not gui.light_window_icon.isNull():
        dialog.setWindowIcon(gui.light_window_icon)
    dialog.setWindowTitle(t("Normalizer Dictionary"))
    dialog.setModal(True)
    dialog.resize(700, 520)
    dialog.setStyleSheet("""
            QDialog { background-color: #0f1724; }
            QLabel { color: #d7e3f4; background-color: transparent; }
            QTableWidget { background-color: #132033; color: #d7e3f4; gridline-color: #2f4868;
                border: 1px solid #2f4868; border-radius: 8px; font-size: 13px; }
            QTableWidget::item:selected { background-color: #29405d; color: #f8fbff; }
            QHeaderView::section { background-color: #1a2c44; color: #8ad7ff; border: none;
                padding: 6px 8px; font-weight: 700; font-size: 12px; }
            QPushButton { background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
                border-radius: 8px; padding: 6px 16px; font-weight: 600; }
            QPushButton:hover { background-color: #29405d; }
            QPushButton#dangerBtn { background-color: #5a1a1a; border-color: #8b2a2a; }
            QPushButton#dangerBtn:hover { background-color: #7a2828; }
            QPushButton#primaryBtn { background-color: #1a4a5a; border-color: #2a6a8b; }
            QPushButton#primaryBtn:hover { background-color: #1e5a6e; }
            QTabWidget::pane { border: 1px solid #2f4868; background-color: #0f1724; border-radius: 8px; }
            QTabBar::tab { background-color: #1a2c44; color: #9fb3ca; padding: 8px 20px; border: 1px solid #2f4868;
                border-bottom: none; border-top-left-radius: 8px; border-top-right-radius: 8px; margin-right: 2px; }
            QTabBar::tab:selected { background-color: #132033; color: #8ad7ff; }
        """)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(16, 16, 16, 16)
    layout.setSpacing(12)

    title = QLabel("Manage Normalizer Dictionary", dialog)
    title.setStyleSheet("color: #f8fbff; font-size: 16px; font-weight: 700;")
    layout.addWidget(title)

    project_name = str(getattr(state, "project_id", "") or "current project")
    hint = QLabel(
        t(
            "Project: {project_name}\nEntries affect Piper pronunciation only; translated subtitle text is not changed.",
            project_name=project_name,
        ),
        dialog,
    )
    hint.setStyleSheet("color: #9fb3ca; font-size: 12px;")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    from PySide6.QtWidgets import QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView
    tabs = QTabWidget(dialog)
    layout.addWidget(tabs, 1)

    tables = {}

    for defn in DICT_DEFS:
        tab = QWidget(dialog)
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(8)

        table = QTableWidget(0, 2, dialog)
        table.setHorizontalHeaderLabels([t(defn["col_a"].title()), t(defn["col_b"].title())])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.verticalHeader().setVisible(False)
        tab_layout.addWidget(table, 1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        add_btn = QPushButton("+ Add Row", dialog)
        remove_btn = QPushButton("Remove Selected", dialog)
        remove_btn.setObjectName("dangerBtn")
        btn_row.addWidget(add_btn)
        btn_row.addWidget(remove_btn)
        btn_row.addStretch()
        tab_layout.addLayout(btn_row)

        add_btn.clicked.connect(lambda _checked=False, t=table: _add_dictionary_row(t))
        remove_btn.clicked.connect(lambda _checked=False, t=table: _remove_dictionary_rows(t))

        tables[defn["key"]] = {"table": table, "defn": defn}
        tabs.addTab(tab, defn["label"])

    bottom_row = QHBoxLayout()
    bottom_row.setSpacing(8)
    bottom_row.addStretch()

    save_btn = QPushButton("Save All", dialog)
    save_btn.setObjectName("primaryBtn")
    close_btn = QPushButton("Close", dialog)
    close_btn.clicked.connect(dialog.accept)
    bottom_row.addWidget(save_btn)
    bottom_row.addWidget(close_btn)
    layout.addLayout(bottom_row)

    def _load_all():
        settings = getattr(state, "settings", {}) or {}
        dictionary = settings.get("normalizer_dictionary", {}) or {}
        for key, meta in tables.items():
            rows = dictionary.get(key, []) if isinstance(dictionary, dict) else []
            table = meta["table"]
            table.setRowCount(0)
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows if isinstance(rows, (list, tuple)) else []:
                if not isinstance(row, dict):
                    continue
                a = str(row.get(meta["defn"]["col_a"], row.get("key", "")) or "").strip()
                b = str(row.get(meta["defn"]["col_b"], row.get("value", "")) or "").strip()
                if a or b:
                    r = table.rowCount()
                    table.insertRow(r)
                    table.setItem(r, 0, QTableWidgetItem(a))
                    table.setItem(r, 1, QTableWidgetItem(b))

    def _save_all():
        dictionary = {}
        for key, meta in tables.items():
            table = meta["table"]
            rows = []
            for r in range(table.rowCount()):
                a = (table.item(r, 0).text() if table.item(r, 0) else "").strip()
                b = (table.item(r, 1).text() if table.item(r, 1) else "").strip()
                if a or b:
                    rows.append({meta["defn"]["col_a"]: a, meta["defn"]["col_b"]: b})
            dictionary[key] = rows
        try:
            state.set_setting("normalizer_dictionary", dictionary)
            gui.current_project_state = state
            gui.project_service.save_project(state)
            try:
                from tts_processor import reset_vietnamese_normalizer_cache
                reset_vietnamese_normalizer_cache()
            except Exception as exc:
                gui.log(f"[VietDict] Cache refresh warning: {exc}")
            gui._voiceover_force_refresh = True
            gui._pending_voice_signature = ""
            gui.log(f"[VietDict] Saved project pronunciation dictionary ({project_name}).")
            gui.refresh_ui_state()
            QMessageBox.information(dialog, t("Normalizer Dictionary"), t("Project dictionary saved. New Preview Voice, Regenerate Voice, and TTS runs will use it immediately."))
        except Exception as exc:
            gui.log(f"[VietDict] Save failed: {exc}")
            QMessageBox.critical(dialog, t("Normalizer Dictionary"), f"{t('Could not save the project dictionary. No pronunciation changes were applied.')}\n\n{exc}")

    save_btn.clicked.connect(_save_all)

    _load_all()
    dialog.exec()
