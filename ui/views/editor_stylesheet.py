"""Qt stylesheet for the main video editor."""

EDITOR_STYLESHEET = """
            QMainWindow {
                background-color: #101826;
            }
            QWidget {
                color: #dbe5f3;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            #centralWidget {
                background-color: #101826;
            }
            #leftPanelArea {
                background-color: #121b2b;
                border-right: 1px solid #223248;
            }
            #leftPanelContainer {
                background-color: #121b2b;
            }
            #rightPanel {
                background-color: #101826;
            }
            QGroupBox {
                border: none;
                border-radius: 0px;
                margin-top: 0px;
                font-weight: bold;
                color: #f3f7fb;
                background-color: transparent;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #8ad7ff;
            }
            QFrame#heroCard, QFrame#statusCard, QFrame#sideInfoCard {
                background-color: #0d1624;
                border: 1px solid #24384f;
                border-radius: 14px;
            }
            QFrame#audioSourcePanel {
                background-color: #101d2d;
                border: 1px solid #2a455f;
                border-radius: 10px;
            }
            QLabel#audioSourceTitle {
                color: #edf7ff;
                font-weight: 700;
            }
            QFrame#subtitleInspectorHandle {
                background-color: #0d1624;
                border: 1px solid #24384f;
                border-left: none;
                border-top-right-radius: 14px;
                border-bottom-right-radius: 14px;
            }
            QPushButton#subtitleInspectorHandleBtn {
                background-color: #162638;
                color: #8ad7ff;
                border: 1px solid #31506d;
                border-right: none;
                border-top-left-radius: 999px;
                border-bottom-left-radius: 999px;
                border-top-right-radius: 0px;
                border-bottom-right-radius: 0px;
                font-size: 20px;
                font-weight: 900;
                padding: 0px;
            }
            QPushButton#subtitleInspectorHandleBtn:hover {
                background-color: #1d3047;
                border-color: #4d82b5;
            }
            QPushButton#sidebarCollapseTab, QPushButton#inspectorCollapseTab {
                background-color: #162638;
                color: #8ad7ff;
                border: 1px solid #24384f;
                border-radius: 3px;
                font-size: 10px;
                font-weight: 900;
                padding: 0px;
                min-width: 16px;
                max-width: 16px;
            }
            QPushButton#sidebarCollapseTab:hover, QPushButton#inspectorCollapseTab:hover {
                background-color: #1d3047;
                border-color: #4d82b5;
            }
            QLabel#heroTitle {
                font-size: 20px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#heroBody, QLabel#statusBody, QLabel#helperLabel, QLabel#previewContextLabel {
                color: #a9b8cb;
                line-height: 1.35em;
            }
            QLabel#helperLabel[filterModified="true"] {
                color: #8ad7ff;
                font-weight: 700;
            }
            QLabel#sectionTitle {
                font-size: 13px;
                font-weight: 700;
                color: #8ad7ff;
            }
            QLabel#timingChip {
                background-color: #173049;
                color: #9fe5ff;
                border: 1px solid #356081;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#statusHeadline {
                font-size: 16px;
                font-weight: 700;
                color: #f8fbff;
            }
            QLabel#statusPill {
                background-color: #1d3a52;
                color: #9fe5ff;
                border: 1px solid #336180;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#subtitleSummaryBadge {
                background-color: #142e47;
                color: #6ee7d6;
                border: 1px solid #2e597d;
                border-radius: 999px;
                padding: 2px 10px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#ttsStatusBadge {
                background-color: #3b2505;
                color: #fbbf24;
                border: 1px solid #f59e0b;
                border-radius: 999px;
                padding: 2px 8px;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel#statusChip {
                background-color: #152537;
                color: #dbe5f3;
                border: 1px solid #2e4764;
                border-radius: 999px;
                padding: 4px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#statusChip[state="ok"] {
                background-color: #153528;
                color: #c8f7df;
                border: 1px solid #2f7a55;
            }
            QLabel#statusChip[state="running"] {
                background-color: #3a2d12;
                color: #ffe29a;
                border: 1px solid #9b7530;
            }
            QLabel#statusChip[state="na"] {
                background-color: #1c2430;
                color: #9fb3ca;
                border: 1px solid #3a4a5f;
            }
            QLabel#statusChip[state="pending"] {
                background-color: #152537;
                color: #dbe5f3;
                border: 1px solid #2e4764;
            }
            QPushButton {
                background-color: #213248;
                color: #ffffff;
                border: 1px solid #304b69;
                border-radius: 10px;
                padding: 8px 14px;
                font-weight: bold;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #2d4665;
                border-color: #4575a8;
            }
            QPushButton#mainActionBtn, QToolButton#mainActionBtn {
                background-color: #4ed0b3;
                color: #0b1620;
                border: none;
                border-radius: 10px;
                font-size: 13px;
                font-weight: bold;
                border-bottom: 2px solid #258971;
                padding: 8px 14px;
            }
            QPushButton#mainActionBtn:hover, QToolButton#mainActionBtn:hover {
                background-color: #66ddc2;
            }
            QToolButton#mainActionBtn::menu-indicator {
                image: none;
                width: 0px;
            }
            QPushButton#secondaryActionBtn {
                background-color: #18314a;
                color: #dff4ff;
                border: 1px solid #4f88b4;
                font-size: 13px;
                font-weight: 700;
                padding: 8px 14px;
            }
            QPushButton#secondaryActionBtn:hover {
                background-color: #21405f;
                border-color: #69a9dc;
            }
            QPushButton#secondaryActionBtn::menu-indicator {
                width: 0px;
                image: none;
            }
            QMenu#headerMoreMenu, QMenu#generateMenu, QMenu#generateStepMenu {
                background-color: #0f1724;
                color: #e6eef9;
                border: 1px solid #30425b;
                padding: 6px;
            }
            QMenu#headerMoreMenu::item, QMenu#generateMenu::item, QMenu#generateStepMenu::item {
                background-color: transparent;
                color: #e6eef9;
                padding: 8px 14px;
                border-radius: 8px;
            }
            QMenu#generateStepMenu::item:enabled {
                background-color: #17324a;
                color: #e8f7ff;
                border: 1px solid #39749e;
                font-weight: 700;
            }
            QMenu#generateStepMenu::item:disabled {
                background-color: #111b29;
                color: rgba(151, 169, 190, 110);
                border: 1px solid #1d2a3a;
                font-weight: 400;
            }
            QMenu#headerMoreMenu::item:selected, QMenu#generateMenu::item:selected, QMenu#generateStepMenu::item:selected {
                background-color: #213248;
                color: #ffffff;
            }
            QMenu#generateStepMenu::item:disabled:selected {
                background-color: #111b29;
                color: rgba(151, 169, 190, 110);
            }
            QMenu#headerMoreMenu::separator, QMenu#generateMenu::separator, QMenu#generateStepMenu::separator {
                height: 1px;
                background: #2b425c;
                margin: 6px 8px;
            }
            QPushButton#workflowTabBtn {
                background-color: #162638;
                color: #9fb3ca;
                border: 1px solid #2b425c;
                border-radius: 10px;
                padding: 5px 9px;
                font-size: 10px;
                font-weight: 700;
            }
            QPushButton#workflowTabBtn:hover {
                background-color: #1c3047;
                border-color: #44698f;
            }
            QPushButton#workflowTabBtn:checked {
                background-color: #24425f;
                color: #f8fbff;
                border-color: #5fb9ff;
            }
            QStackedWidget#leftPanelStack {
                background: transparent;
            }
            QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                background-color: #111927;
                border: 1px solid #31445d;
                border-radius: 10px;
                color: #ffffff;
                padding: 8px;
            }
            QScrollArea#segmentEditorScroll {
                background-color: transparent;
                border: none;
            }
            QWidget#segmentEditorContainer {
                background-color: transparent;
            }
            QFrame#segmentInspectorCard {
                background-color: #0d1624;
                border: 1px solid #24384f;
                border-radius: 0px;
            }
            QTextEdit#segmentInspectorEditor {
                background-color: #111b2b;
                border: 1px solid #35506f;
                border-radius: 10px;
                padding: 10px 12px;
            }
            QTextEdit#segmentInspectorEditor:focus {
                border: 1px solid #5fb9ff;
                background-color: #122033;
            }
            QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
                border: 1px solid #8ad7ff;
            }
            QLineEdit:disabled, QTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
                background-color: #0d1420;
                color: #8b9bb0;
                border: 1px solid #243447;
            }
            QLineEdit::placeholder, QTextEdit {
                selection-background-color: #325173;
            }
            QProgressBar {
                border: 1px solid #2a3a50;
                border-radius: 10px;
                text-align: center;
                background-color: #111927;
                color: white;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #5ed5c9, stop:1 #2b9f96);
                border-radius: 10px;
            }
            QLabel {
                background: transparent;
                color: #dbe5f3;
                font-size: 12px;
            }
            QCheckBox {
                background: transparent;
                color: #dbe5f3;
            }
            QRadioButton {
                background: transparent;
                color: #dbe5f3;
            }
            QScrollArea {
                border: none;
                background-color: #121b2b;
            }
            QScrollBar:vertical {
                border: none;
                background: #142030;
                width: 12px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: #35506f;
                min-height: 20px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #416287;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            /* Fix ComboBox Dropdown colors */
            QComboBox QAbstractItemView {
                background-color: #111927;
                color: #ffffff;
                selection-background-color: #325173;
                border: 1px solid #31445d;
                outline: none;
            }
            QMessageBox {
                background-color: #101826;
            }
            QMessageBox QLabel {
                color: #e6eef9;
                background: transparent;
            }
            QMessageBox QPushButton {
                min-width: 96px;
            }
            QTabWidget::pane {
                border: 1px solid #30425b;
                border-radius: 12px;
                background: #111927;
                top: -1px;
            }
            QTabBar::tab {
                background: #1d2c40;
                color: #a8bad2;
                padding: 9px 14px;
                border: 1px solid #30425b;
                border-bottom: none;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                min-width: 110px;
            }
            QTabBar::tab:selected {
                background: #111927;
                color: #8ad7ff;
            }
        """
