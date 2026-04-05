from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, 
    QProgressBar, QFrame, QScrollArea, QWidget, 
    QGraphicsDropShadowEffect, QPushButton
)
import time
from PySide6.QtCore import Qt, QTimer, Property, QRect
from PySide6.QtGui import QColor, QFont, QPainter, QLinearGradient, QBrush, QPen

class StepWidget(QFrame):
    def __init__(self, name, parent=None):
        super().__init__(parent)
        self.setObjectName("stepWidget")
        self.status = "pending"
        self.setStyleSheet("""
            #stepWidget {
                background-color: rgba(30, 30, 30, 180);
                border: 1px solid rgba(255, 255, 255, 10);
                border-radius: 12px;
                padding: 10px;
                margin-bottom: 6px;
            }
            QLabel {
                color: #e0e0e0;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            #stepName {
                font-size: 14px;
                font-weight: 600;
            }
            #stepStatus {
                font-size: 12px;
                font-weight: 500;
            }
            #stepTime {
                color: #888;
                font-family: 'Consolas', monospace;
                font-size: 11px;
            }
        """)
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(15, 12, 15, 12)
        
        self.indicator = QWidget()
        self.indicator.setFixedSize(10, 10)
        self.indicator.setStyleSheet("background-color: #444; border-radius: 5px;")
        layout.addWidget(self.indicator)
        layout.addSpacing(10)
        
        self.name_label = QLabel(name)
        self.name_label.setObjectName("stepName")
        layout.addWidget(self.name_label)
        
        layout.addStretch()
        
        self.status_label = QLabel("Pending")
        self.status_label.setObjectName("stepStatus")
        self.status_label.setFixedWidth(90)
        self.status_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.status_label)
        
        self.time_label = QLabel("00:00")
        self.time_label.setObjectName("stepTime")
        self.time_label.setFixedWidth(50)
        self.time_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        layout.addWidget(self.time_label)
        
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._update_time)
        self.elapsed = 0
        self.start_time = None
        
        # Pulse animation for running state
        self.pulse_timer = QTimer(self)
        self.pulse_timer.setInterval(800)
        self.pulse_timer.timeout.connect(self._toggle_pulse)
        self._pulse_state = False

    def set_status(self, status):
        self.status = status
        if status == "running":
            self.status_label.setText("Processing")
            self.status_label.setStyleSheet("color: #00E5FF;")
            self.indicator.setStyleSheet("background-color: #00E5FF; border: 2px solid rgba(0, 229, 255, 50);")
            self.setStyleSheet(self.styleSheet() + "#stepWidget { border: 1px solid rgba(0, 229, 255, 80); background-color: rgba(0, 229, 255, 15); }")
            self.start_time = time.monotonic()
            self.start_timer()
            self.pulse_timer.start()
        elif status == "done":
            self.status_label.setText("Completed")
            self.status_label.setStyleSheet("color: #00FF88;")
            self.indicator.setStyleSheet("background-color: #00FF88;")
            self.setStyleSheet(self.styleSheet().replace("rgba(0, 229, 255, 80)", "rgba(0, 255, 136, 40)").replace("rgba(0, 229, 255, 15)", "rgba(0, 0, 0, 180)"))
            self.stop_timer()
            self.pulse_timer.stop()
        elif status == "failed":
            self.status_label.setText("Failed")
            self.status_label.setStyleSheet("color: #FF4444;")
            self.indicator.setStyleSheet("background-color: #FF4444;")
            self.setStyleSheet(self.styleSheet() + "#stepWidget { border: 1px solid rgba(255, 68, 68, 80); background-color: rgba(255, 68, 68, 15); }")
            self.stop_timer()
            self.pulse_timer.stop()
        else:
            self.status_label.setText("Pending")
            self.status_label.setStyleSheet("color: #666;")
            self.indicator.setStyleSheet("background-color: #444; border-radius: 5px;")
            self.start_time = None
            self.elapsed = 0
            self.time_label.setText("00:00")

    def start_timer(self):
        self._update_time()
        self.timer.start()
        
    def stop_timer(self):
        if self.start_time is not None:
            self.elapsed = int(time.monotonic() - self.start_time)
            mins = self.elapsed // 60
            secs = self.elapsed % 60
            self.time_label.setText(f"{mins:02d}:{secs:02d}")
            self.start_time = None
        self.timer.stop()

    def _update_time(self):
        self.elapsed = int(time.monotonic() - self.start_time) if self.start_time is not None else self.elapsed + 1
        mins = self.elapsed // 60
        secs = self.elapsed % 60
        self.time_label.setText(f"{mins:02d}:{secs:02d}")
        
    def _toggle_pulse(self):
        self._pulse_state = not self._pulse_state
        alpha = 255 if self._pulse_state else 150
        self.indicator.setStyleSheet(f"background-color: rgba(0, 229, 255, {alpha}); border-radius: 5px;")

class PipelineProgressDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CapCap AI Pipeline")
        self.setFixedSize(580, 700)
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.workflow_start_time = None
        self.total_timer = QTimer(self)
        self.total_timer.setInterval(1000)
        self.total_timer.timeout.connect(self._update_total_time)
        
        self.main_frame = QFrame(self)
        self.main_frame.setObjectName("mainFrame")
        self.main_frame.setFixedSize(560, 680)
        self.main_frame.move(10, 10)
        self.main_frame.setStyleSheet("""
            #mainFrame {
                background-color: #0f1724;
                border: 1px solid #28364c;
                border-radius: 20px;
            }
        """)
        
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setXOffset(0)
        shadow.setYOffset(10)
        shadow.setColor(QColor(0, 0, 0, 180))
        self.main_frame.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.main_frame)
        layout.setContentsMargins(30, 30, 30, 30)
        
        header_layout = QHBoxLayout()
        self.title_label = QLabel("AI Production Pipeline")
        self.title_label.setStyleSheet("font-size: 20px; font-weight: 800; color: white;")
        header_layout.addWidget(self.title_label)
        
        self.close_btn = QPushButton("✕", self)
        self.close_btn.setFixedSize(30, 30)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: none;
                color: #666;
                font-size: 18px;
                border: none;
            }
            QPushButton:hover {
                color: white;
            }
        """)
        self.close_btn.clicked.connect(self.hide)
        header_layout.addStretch()
        header_layout.addWidget(self.close_btn)
        layout.addLayout(header_layout)
        
        self.overall_progress = QProgressBar()
        self.overall_progress.setFixedHeight(8)
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setValue(0)
        self.overall_progress.setTextVisible(False)
        self.overall_progress.setStyleSheet("""
            QProgressBar {
                background: #222;
                border: none;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00E5FF, stop:1 #00FF88);
                border-radius: 4px;
            }
        """)
        layout.addSpacing(10)
        layout.addWidget(self.overall_progress)
        
        layout.addSpacing(20)
        
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(0, 10, 5, 0)
        self.scroll_layout.setSpacing(2)
        self.scroll_layout.addStretch()
        
        self.scroll.setWidget(self.scroll_content)
        self.scroll.setStyleSheet("background: transparent;")
        layout.addWidget(self.scroll)
        
        self.steps = {}
        self.step_order = []
        
        self.footer = QLabel("Initializing workflow engine...")
        self.footer.setStyleSheet("color: #888; font-size: 13px; margin-top: 15px;")
        layout.addWidget(self.footer)

        self.total_time_label = QLabel("Total time: 00:00")
        self.total_time_label.setStyleSheet("color: #d7e3f4; font-size: 12px; margin-top: 6px;")
        layout.addWidget(self.total_time_label)

        self.dismiss_btn = QPushButton("Close")
        self.dismiss_btn.setFixedHeight(34)
        self.dismiss_btn.setStyleSheet("""
            QPushButton {
                background-color: #162133;
                color: #dbe5f3;
                border: 1px solid #31445d;
                border-radius: 10px;
                font-weight: 700;
                padding: 6px 16px;
            }
            QPushButton:hover {
                background-color: #1f2b3d;
            }
        """)
        self.dismiss_btn.clicked.connect(self.hide)
        layout.addWidget(self.dismiss_btn)

    def add_step(self, step_id, name):
        widget = StepWidget(name)
        self.steps[step_id] = widget
        self.step_order.append(step_id)
        self.scroll_layout.insertWidget(len(self.step_order) - 1, widget)
        return widget

    def start_step(self, step_id):
        if step_id in self.steps:
            if self.workflow_start_time is None:
                self.workflow_start_time = time.monotonic()
                self.total_timer.start()
            self.steps[step_id].set_status("running")
            idx = self.step_order.index(step_id)
            val = int((idx / len(self.step_order)) * 100)
            self.overall_progress.setValue(val)
            self.footer.setText(f"Stage {idx+1}/{len(self.step_order)}: {self.steps[step_id].name_label.text()}")

    def finish_step(self, step_id):
        if step_id in self.steps:
            self.steps[step_id].set_status("done")
            idx = self.step_order.index(step_id)
            val = int(((idx + 1) / len(self.step_order)) * 100)
            self.overall_progress.setValue(val)

    def fail_step(self, step_id):
        if step_id in self.steps:
            self.steps[step_id].set_status("failed")
            self.footer.setText(f"Error encountered during: {self.steps[step_id].name_label.text()}")
            self.footer.setStyleSheet("color: #FF4444; font-weight: bold; margin-top: 15px;")
            self._stop_total_timer()

    def set_completed(self):
        self.overall_progress.setValue(100)
        self.footer.setText("✨ Pipeline execution complete! Video is ready.")
        self.footer.setStyleSheet("color: #00FF88; font-weight: bold; font-size: 14px; margin-top: 15px;")
        self._stop_total_timer()

    def _update_total_time(self):
        if self.workflow_start_time is None:
            return
        elapsed = int(time.monotonic() - self.workflow_start_time)
        mins = elapsed // 60
        secs = elapsed % 60
        self.total_time_label.setText(f"Total time: {mins:02d}:{secs:02d}")

    def _stop_total_timer(self):
        if self.total_timer.isActive():
            self.total_timer.stop()
        if self.workflow_start_time is not None:
            elapsed = int(time.monotonic() - self.workflow_start_time)
            mins = elapsed // 60
            secs = elapsed % 60
            self.total_time_label.setText(f"Total time: {mins:02d}:{secs:02d}")

    # Drag support for frameless window
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            self.move(self.pos() + event.globalPosition().toPoint() - self._drag_pos)
            self._drag_pos = event.globalPosition().toPoint()
            event.accept()
