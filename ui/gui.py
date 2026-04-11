import os
import shutil
import sys

from PySide6.QtWidgets import QApplication

from main_window import VideoTranslatorGUI

__all__ = ["VideoTranslatorGUI"]


def _app_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _bootstrap_env(app_root: str) -> None:
    env_path = os.path.join(app_root, ".env")
    env_example_path = os.path.join(app_root, ".env_example")

    if not os.path.exists(env_path) and os.path.exists(env_example_path):
        shutil.copyfile(env_example_path, env_path)

    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            os.environ.setdefault(key, value.strip())


if __name__ == "__main__":
    app_root = _app_root()
    _bootstrap_env(app_root)
    os.chdir(app_root)
    app = QApplication(sys.argv)
    window = VideoTranslatorGUI()
    window.show()
    sys.exit(app.exec())
