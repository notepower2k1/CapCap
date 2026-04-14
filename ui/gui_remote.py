import os

os.environ.setdefault("CAPCAP_RUNTIME_PROFILE", "remote")

from gui import VideoTranslatorGUI, _app_root, _bootstrap_env  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
import sys  # noqa: E402


if __name__ == "__main__":
    app_root = _app_root()
    _bootstrap_env(app_root)
    os.chdir(app_root)
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())
