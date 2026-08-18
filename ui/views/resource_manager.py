import os

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from runtime_paths import workspace_root as default_workspace_root
from services import ResourceDownloadService


_STATUS_STYLES = {
    "installed": ("Ready", "#3ddc97", "#1b3b2c"),
    "partial":   ("Partial", "#f0b347", "#3b2f12"),
    "missing":   ("Missing", "#ff6b6b", "#3b1a1a"),
}


def _status_pill_widget(status_key: str, parent: QWidget, label_override: str = "") -> QLabel:
    status = str(status_key or "").strip().lower()
    default_label, fg, bg = _STATUS_STYLES.get(status, _STATUS_STYLES["missing"])
    label = str(label_override or default_label).strip() or default_label
    pill = QLabel(label, parent)
    pill.setAlignment(Qt.AlignCenter)
    pill.setStyleSheet(
        f"color: {fg}; background-color: {bg}; border: 1px solid {fg}55;"
        f" border-radius: 10px; padding: 3px 12px; font-weight: 700; font-size: 12px;"
    )
    pill.setFixedHeight(24)
    return pill


def _open_url(url: str) -> bool:
    target = str(url or "").strip()
    if not target:
        return False
    return QDesktopServices.openUrl(QUrl(target))


def _open_folder_dialog(gui_parent, path: str) -> None:
    target = str(path or "").strip()
    if not target:
        return
    from PySide6.QtWidgets import QMessageBox
    try:
        os.makedirs(target, exist_ok=True)
        os.startfile(os.path.abspath(target))
    except Exception as exc:
        QMessageBox.critical(gui_parent, "Error", f"Could not open folder:\n{exc}")


def open_resource_manager(workspace_root: str = None, parent=None,
                          on_finished=None):
    if workspace_root is None:
        workspace_root = default_workspace_root()

    service = ResourceDownloadService(workspace_root)

    dialog = QDialog(parent)
    dialog.setWindowTitle("Manage Resources")
    dialog.setModal(True)
    dialog.resize(820, 580)
    dialog.setStyleSheet("""
        QDialog { background-color: #0f1724; }
        QLabel { color: #d7e3f4; background-color: transparent; }
        QLabel#resourceTitle { color: #f8fbff; font-size: 16px; font-weight: 700; }
        QLabel#resourceHint { color: #9fb3ca; font-size: 12px; }
        QWidget#resourceContent { background-color: transparent; }
        QScrollArea { border: none; background-color: #0f1724; }
        QFrame#resourceCard { background-color: #132033; border: 1px solid #2f4868; border-radius: 12px; }
        QPushButton {
            background-color: #22344d; color: #f8fbff; border: 1px solid #34506f;
            border-radius: 8px; padding: 6px 14px; font-weight: 600; min-width: 90px;
        }
        QPushButton:hover { background-color: #29405d; }
        QPushButton:disabled { color: #8ea3bb; background-color: #182636; border-color: #29405d; }
        QPushButton#primaryBtn {
            background-color: #1a3a5c; color: #8ad7ff; border: 1px solid #4ecdc4;
            font-weight: 700;
        }
        QPushButton#primaryBtn:hover { background-color: #224a6e; }
    """)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(18, 18, 18, 18)
    layout.setSpacing(12)

    title = QLabel("Manage Resources", dialog)
    title.setObjectName("resourceTitle")
    layout.addWidget(title)

    hint = QLabel(
        "Each resource shows its target folder and download link. "
        "Download the file yourself and drop it into the target folder. "
        "Use 'Refresh' to re-check status.",
        dialog,
    )
    hint.setObjectName("resourceHint")
    hint.setWordWrap(True)
    layout.addWidget(hint)

    scroll = QScrollArea(dialog)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    layout.addWidget(scroll, 1)

    content = QWidget(dialog)
    content.setObjectName("resourceContent")
    content_layout = QVBoxLayout(content)
    content_layout.setContentsMargins(0, 0, 0, 0)
    content_layout.setSpacing(10)
    scroll.setWidget(content)

    dialog._resource_rows = {}

    def _show_status_pill(row, status_key: str, status_label: str = ""):
        new_pill = _status_pill_widget(status_key, dialog, status_label)
        row["header_row"].replaceWidget(row["status_pill"], new_pill)
        row["status_pill"].deleteLater()
        row["status_pill"] = new_pill
        row["status_pill"].show()

    def _add_card(item, target_layout):
        card = QFrame(dialog)
        card.setObjectName("resourceCard")
        outer = QVBoxLayout(card)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        header_row = QHBoxLayout()
        header_row.setSpacing(8)
        name_label = QLabel(str(item.get("name", item.get("id", "Resource"))), dialog)
        name_label.setStyleSheet("color: #f8fbff; font-weight: 700; font-size: 14px; background-color: transparent;")
        header_row.addWidget(name_label, 1)

        required_for = str(item.get("required_for", "") or "").strip()
        if required_for:
            required_label = QLabel(f"Required for {required_for}", dialog)
            required_label.setStyleSheet(
                "color: #ffd28a; font-size: 10px; font-weight: 700; "
                "background-color: #4a3520; border: 1px solid #8b6734; "
                "border-radius: 6px; padding: 2px 6px;"
            )
            header_row.addWidget(required_label, 0, Qt.AlignVCenter)

        status_pill = _status_pill_widget(
            item.get("status", "missing"),
            dialog,
            item.get("status_label", ""),
        )
        header_row.addWidget(status_pill, 0, Qt.AlignVCenter | Qt.AlignRight)

        outer.addLayout(header_row)

        description = str(item.get("description", "")).strip()
        if description:
            desc_label = QLabel(description, dialog)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet("color: #c0d0e3; font-size: 12px; background-color: transparent;")
            outer.addWidget(desc_label)

        target_dir = str(item.get("target_dir", "")).strip()
        if target_dir:
            path_row = QHBoxLayout()
            path_row.setSpacing(6)
            path_label = QLabel("Target folder:", dialog)
            path_label.setStyleSheet("color: #8ea3bb; font-size: 11px; background-color: transparent;")
            path_value = QLabel(target_dir, dialog)
            path_value.setStyleSheet("color: #d7e3f4; font-size: 11px; font-family: monospace; background-color: transparent;")
            path_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path_value.setWordWrap(True)
            path_row.addWidget(path_label, 0)
            path_row.addWidget(path_value, 1)
            outer.addLayout(path_row)

        expected = str(item.get("expected_filename", "")).strip()
        if expected:
            file_row = QHBoxLayout()
            file_row.setSpacing(6)
            file_caption = QLabel("Expected file:", dialog)
            file_caption.setStyleSheet("color: #8ea3bb; font-size: 11px; background-color: transparent;")
            file_value = QLabel(expected, dialog)
            file_value.setStyleSheet("color: #d7e3f4; font-size: 11px; font-family: monospace; background-color: transparent;")
            file_value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            file_value.setWordWrap(True)
            file_row.addWidget(file_caption, 0)
            file_row.addWidget(file_value, 1)
            outer.addLayout(file_row)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)

        download_url = str(item.get("download_url", "")).strip()
        download_btn = QPushButton("Open Download Page", dialog)
        download_btn.setObjectName("primaryBtn")
        download_btn.setEnabled(bool(download_url))
        if download_url:
            download_btn.setToolTip(download_url)
        download_btn.clicked.connect(
            lambda _checked=False, url=download_url: _open_url(url)
        )
        button_row.addWidget(download_btn)

        open_folder_btn = QPushButton("Open Storage Folder", dialog)
        open_folder_btn.setEnabled(bool(target_dir))
        open_folder_btn.clicked.connect(
            lambda _checked=False, p=target_dir: _open_folder_dialog(dialog, p)
        )
        button_row.addWidget(open_folder_btn)

        outer.addLayout(button_row)

        target_layout.addWidget(card)
        dialog._resource_rows[item["id"]] = {
            "item": item,
            "name_label": name_label,
            "status_pill": status_pill,
            "header_row": header_row,
        }

    def _make_section(title_text, expanded):
        wrapper = QFrame()
        wrapper.setObjectName("statusCard")
        wrapper.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        wrapper_layout = QVBoxLayout(wrapper)
        wrapper_layout.setContentsMargins(8, 2, 8, 2)
        wrapper_layout.setSpacing(0)

        btn = QToolButton()
        btn.setText(("▼ " if expanded else "▶ ") + title_text)
        btn.setCheckable(True)
        btn.setChecked(expanded)
        btn.setToolButtonStyle(Qt.ToolButtonTextOnly)
        btn.setStyleSheet("QToolButton { text-align: left; font-weight: 700; color: #8ad7ff; border: none; padding: 2px 4px; margin: 0; }")

        inner = QWidget()
        inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        inner_layout = QVBoxLayout(inner)
        inner_layout.setContentsMargins(0, 0, 0, 4)
        inner_layout.setSpacing(2)
        inner.setVisible(expanded)
        if not expanded:
            inner.setMaximumHeight(0)

        btn.toggled.connect(lambda c: (
            btn.setText(("▼ " if c else "▶ ") + title_text),
            inner.setVisible(c),
            inner.setMaximumHeight(16777215 if c else 0),
        ))
        wrapper_layout.addWidget(btn)
        wrapper_layout.addWidget(inner)
        return wrapper, inner_layout

    def _refresh():
        resources = {item["id"]: item for item in service.list_resources()}
        for resource_id, row in dialog._resource_rows.items():
            item = resources.get(resource_id, row.get("item", {}))
            row["item"] = item
            status = str(item.get("status", "missing")).strip().lower()
            _show_status_pill(row, status, str(item.get("status_label", "") or ""))

    def _populate():
        for i in reversed(range(content_layout.count())):
            it = content_layout.takeAt(i)
            widget = it.widget() if it is not None else None
            if widget is not None:
                widget.deleteLater()
        dialog._resource_rows = {}

        resources = service.list_resources()
        cpu_kinds = {"sensevoice", "whisper_cpu"}
        gpu_kinds = {"ai", "whisper", "cuda"}
        diarization_kinds = {"diarization"}
        voice_kinds = {"voice"}

        cpu_items = [r for r in resources if r.get("kind") in cpu_kinds]
        gpu_items = [r for r in resources if r.get("kind") in gpu_kinds]
        diarization_items = [r for r in resources if r.get("kind") in diarization_kinds]
        voice_items = [r for r in resources if r.get("kind") in voice_kinds]

        # A resource whose kind matched no section used to be dropped without
        # a trace, which is how the speaker-diarization entries stayed
        # invisible while still being offered by the service. Collect the
        # leftovers so adding a resource can never silently hide it again.
        known_kinds = cpu_kinds | gpu_kinds | diarization_kinds | voice_kinds
        other_items = [r for r in resources if r.get("kind") not in known_kinds]

        if cpu_items:
            cpu_card, cpu_layout = _make_section("CPU Resource", expanded=True)
            for item in cpu_items:
                _add_card(item, cpu_layout)
            content_layout.addWidget(cpu_card)

        if gpu_items:
            # GPU resources remain relevant in CPU Mode: users need a clear
            # place to find the GPU Acceleration Pack before switching modes.
            # Previously this header was conditionally removed based on the
            # process environment, which made "GPU Resource" appear to vanish
            # after launcher/device-state refreshes.
            gpu_card, gpu_layout = _make_section("GPU Resource", expanded=False)
            for item in gpu_items:
                _add_card(item, gpu_layout)
            content_layout.addWidget(gpu_card)

        if diarization_items:
            # Optional: diarization only affects per-speaker voice assignment,
            # so it starts collapsed like the GPU section.
            diarization_card, diarization_layout = _make_section(
                "Speaker Diarization (optional)", expanded=False
            )
            for item in diarization_items:
                _add_card(item, diarization_layout)
            content_layout.addWidget(diarization_card)

        for item in voice_items:
            _add_card(item, content_layout)

        if other_items:
            other_card, other_layout = _make_section("Other Resources", expanded=False)
            for item in other_items:
                _add_card(item, other_layout)
            content_layout.addWidget(other_card)

        content_layout.addStretch()

    _populate()

    footer_row = QHBoxLayout()
    footer_row.setSpacing(8)
    footer_hint = QLabel(
        "Tip: target folders are created automatically when you open them.",
        dialog,
    )
    footer_hint.setObjectName("resourceHint")
    footer_hint.setWordWrap(True)
    footer_row.addWidget(footer_hint, 1)

    refresh_btn = QPushButton("Refresh", dialog)
    refresh_btn.clicked.connect(_refresh)
    footer_row.addWidget(refresh_btn)

    close_btn = QPushButton("Close", dialog)
    close_btn.clicked.connect(dialog.accept)
    footer_row.addWidget(close_btn)

    layout.addLayout(footer_row)

    def _on_dialog_closed():
        if on_finished:
            try:
                on_finished()
            except Exception:
                pass

    dialog.accepted.connect(_on_dialog_closed)
    dialog.rejected.connect(_on_dialog_closed)
    dialog.exec()
