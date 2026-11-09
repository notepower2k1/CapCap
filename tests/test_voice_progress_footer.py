import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QProgressBar
from ui.main_window import VideoTranslatorGUI
from ui.i18n import get_language, set_language

APP = QApplication.instance() or QApplication([])


class CountingLabel(QLabel):
    def __init__(self):
        super().__init__()
        self.updates = 0

    def setText(self, text):
        self.updates += 1
        super().setText(text)


class TestVoiceProgressFooter(unittest.TestCase):
    def test_repeated_omnivoice_notices_keep_footer_count_without_redrawing(self):
        language = get_language()
        set_language("vi")
        footer, status = CountingLabel(), QLabel()
        dialog = SimpleNamespace(
            footer=footer, isVisible=lambda: True,
            steps={"voiceover": SimpleNamespace(status_label=status)},
            step_order=["voiceover"], overall_progress=QProgressBar(),
        )
        gui = SimpleNamespace(log=MagicMock(), pipeline_controller=SimpleNamespace(progress_dialog=dialog), progress_bar=QProgressBar())
        def update(message):
            VideoTranslatorGUI._on_voiceover_progress(gui, message)
        try:
            update("Synthesizing with TTS: 0/20 (0.0%)...")
            for _ in range(5):
                update("Generating speech with OmniVoice (audio.cpp)...")
            self.assertEqual(footer.text(), "Đang tạo giọng: 0/20 câu (0%)")
            self.assertEqual(footer.updates, 1)
            self.assertEqual(status.text(), "0%")
            update("Synthesizing with voice 'omnivoice:test': 10/20 (50.0%)...")
            for _ in range(5):
                update("Generating speech with OmniVoice (audio.cpp)...")
            self.assertEqual(footer.text(), "Đang tạo giọng: 10/20 câu (50%)")
            self.assertEqual(footer.updates, 2)
            self.assertEqual(status.text(), "50%")
            update("[TTS Warning] Segment failed")
            self.assertEqual(footer.text(), "[TTS Warning] Segment failed")
        finally:
            set_language(language)
            footer.deleteLater()
            status.deleteLater()

