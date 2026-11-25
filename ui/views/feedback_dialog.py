import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.i18n import t


def show_feedback_dialog(gui):
    """Open the Feedback & Bug Report dialog."""
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtCore import QUrl
    from runtime_paths import workspace_root

    try:
        dialog = QDialog(gui)
    except (RuntimeError, TypeError):
        dialog = QDialog()
    if hasattr(gui, "light_window_icon") and gui.light_window_icon and not gui.light_window_icon.isNull():
        dialog.setWindowIcon(gui.light_window_icon)
    dialog.setWindowTitle(t("Feedback & Bug Report"))
    dialog.setModal(True)
    dialog.setMinimumWidth(580)
    dialog.setStyleSheet(
        """
            QDialog {
                background-color: #0f1724;
            }
            QLabel {
                color: #d7e3f4;
                background: transparent;
            }
            QLabel#statusHeadline {
                color: #f8fbff;
                font-size: 16px;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                color: #60a5fa;
                font-size: 14px;
                font-weight: 600;
            }
            QLabel#helperLabel {
                color: #9fb3ca;
                font-size: 12px;
                line-height: 1.5;
            }
            QFrame[frameClass="feedbackCard"] {
                background-color: #132033;
                border: 1px solid #243b55;
                border-radius: 10px;
            }
            QPushButton {
                background-color: #22344d;
                color: #f8fbff;
                border: 1px solid #34506f;
                border-radius: 8px;
                padding: 8px 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2a4160;
                border-color: #4a729e;
            }
            QPushButton:pressed {
                background-color: #1b2a3d;
            }
            QPushButton#primaryActionBtn {
                background-color: #2563eb;
                border-color: #3b82f6;
            }
            QPushButton#primaryActionBtn:hover {
                background-color: #1d4ed8;
            }
            """
    )
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(14)

    # Header title
    title_label = QLabel(t("💬 Feedback & Support"), dialog)
    title_label.setObjectName("statusHeadline")
    layout.addWidget(title_label)

    # Card 1: Báo lỗi & Gửi log (Bug Report)
    bug_card = QFrame(dialog)
    bug_card.setProperty("frameClass", "feedbackCard")
    bug_card_layout = QVBoxLayout(bug_card)
    bug_card_layout.setContentsMargins(14, 14, 14, 14)
    bug_card_layout.setSpacing(10)

    bug_title = QLabel(t("🐛 Bug Report"), bug_card)
    bug_title.setObjectName("sectionTitle")
    bug_card_layout.addWidget(bug_title)

    log_dir = os.path.join(workspace_root(), "temp")

    bug_text = QLabel(
        t(
            "When encountering an issue, please:<br>"
            "• Get the log file at: <b style='color: #f8fbff;'>CapCap\\temp\\capcap_runtime.log</b><br>"
            "&nbsp;&nbsp;&nbsp;<i>(or click <b style='color: #f8fbff;'>Export Log</b> in the <b style='color: #f8fbff;'>Advanced</b> tab)</i><br>"
            "• Take a screenshot of the error<br>"
            "• Post a description along with the screenshot and log to GitHub Discussions:"
        ),
        bug_card,
    )
    bug_text.setTextFormat(Qt.RichText)
    bug_text.setObjectName("helperLabel")
    bug_text.setWordWrap(True)
    bug_card_layout.addWidget(bug_text)

    link_disc = QLabel(
        '<a style="color: #60a5fa; text-decoration: underline;" href="https://github.com/notepower2k1/CapCap/discussions">https://github.com/notepower2k1/CapCap/discussions</a>',
        bug_card,
    )
    link_disc.setOpenExternalLinks(True)
    bug_card_layout.addWidget(link_disc)

    bug_btn_row = QHBoxLayout()
    bug_btn_row.setSpacing(10)
    export_log_btn = QPushButton(t("💾 Export Log File"), bug_card)
    open_log_btn = QPushButton(t("📂 Open Log Folder (temp)"), bug_card)
    open_disc_btn = QPushButton(t("🌐 Open GitHub Discussions"), bug_card)
    open_disc_btn.setObjectName("primaryActionBtn")

    def _open_log_folder():
        target_dir = log_dir
        if hasattr(gui, "workspace_root") and os.path.exists(os.path.join(gui.workspace_root, "temp", "capcap_runtime.log")):
            target_dir = os.path.join(gui.workspace_root, "temp")
        os.makedirs(target_dir, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(target_dir))

    def _open_discussions():
        QDesktopServices.openUrl(QUrl("https://github.com/notepower2k1/CapCap/discussions"))

    export_log_btn.clicked.connect(gui.export_runtime_logs)
    open_log_btn.clicked.connect(_open_log_folder)
    open_disc_btn.clicked.connect(_open_discussions)
    bug_btn_row.addWidget(export_log_btn)
    bug_btn_row.addWidget(open_log_btn)
    bug_btn_row.addWidget(open_disc_btn)
    bug_btn_row.addStretch()
    bug_card_layout.addLayout(bug_btn_row)

    layout.addWidget(bug_card)

    # Card 2: Đề xuất chức năng mới (Feature Request)
    feat_card = QFrame(dialog)
    feat_card.setProperty("frameClass", "feedbackCard")
    feat_card_layout = QVBoxLayout(feat_card)
    feat_card_layout.setContentsMargins(14, 14, 14, 14)
    feat_card_layout.setSpacing(10)

    feat_title = QLabel(t("💡 Feature Request"), feat_card)
    feat_title.setObjectName("sectionTitle")
    feat_card_layout.addWidget(feat_title)

    feat_text = QLabel(
        t("Have an idea or want to request a new feature for CapCap? Create an issue on GitHub:"),
        feat_card,
    )
    feat_text.setTextFormat(Qt.RichText)
    feat_text.setObjectName("helperLabel")
    feat_text.setWordWrap(True)
    feat_card_layout.addWidget(feat_text)

    link_issues = QLabel(
        '<a style="color: #60a5fa; text-decoration: underline;" href="https://github.com/notepower2k1/CapCap/issues">https://github.com/notepower2k1/CapCap/issues</a>',
        feat_card,
    )
    link_issues.setOpenExternalLinks(True)
    feat_card_layout.addWidget(link_issues)

    feat_btn_row = QHBoxLayout()
    open_issues_btn = QPushButton(t("🚀 Open GitHub Issues"), feat_card)

    def _open_issues():
        QDesktopServices.openUrl(QUrl("https://github.com/notepower2k1/CapCap/issues"))

    open_issues_btn.clicked.connect(_open_issues)
    feat_btn_row.addWidget(open_issues_btn)
    feat_btn_row.addStretch()
    feat_card_layout.addLayout(feat_btn_row)

    layout.addWidget(feat_card)

    # Bottom close button
    bottom_row = QHBoxLayout()
    bottom_row.addStretch()
    close_btn = QPushButton(t("Close"), dialog)
    close_btn.clicked.connect(dialog.accept)
    bottom_row.addWidget(close_btn)
    layout.addLayout(bottom_row)

    dialog.exec()
