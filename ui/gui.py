import sys

from PySide6.QtWidgets import QApplication

from main_window import VideoTranslatorGUI

__all__ = ["VideoTranslatorGUI"]


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())
