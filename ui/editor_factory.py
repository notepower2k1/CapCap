import os
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication


def create_editor(mode=None, on_return=None, on_close=None):
    mode = mode or os.getenv("CAPCAP_UI_MODE","quality")
    if mode == "performance" or on_return is not None:
        from performance_editor import PerformanceEditor, QueueQualityEditor
        editor = PerformanceEditor() if mode == "performance" else QueueQualityEditor()
        if on_return is None:
            def on_return():
                from main_window import _relaunch_launcher
                QTimer.singleShot(0,lambda:_relaunch_launcher())
        editor.returned.connect(on_return)
        editor.exit_requested.connect(on_close or QApplication.quit)
    else:
        from main_window import VideoTranslatorGUI
        editor = VideoTranslatorGUI()
    return editor
