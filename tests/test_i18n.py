import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QLineEdit, QPlainTextEdit, QWidget

from ui import i18n


class MemorySettings:
    def __init__(self):
        self.values = {}

    def value(self, key, default=None):
        return self.values.get(key, default)

    def setValue(self, key, value):
        self.values[key] = value


class I18nTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def tearDown(self):
        i18n.set_language("en", MemorySettings())

    def test_vietnamese_translation_and_template_formatting(self):
        settings = MemorySettings()
        i18n.set_language("vi", settings)

        self.assertEqual(i18n.t("Start Export"), "Bắt đầu xuất")
        self.assertEqual(
            i18n.t("Could not download {resource_id}.", resource_id="cuda:whisper"),
            "Không thể tải cuda:whisper.",
        )

    def test_vietnamese_toolbar_labels_stay_compact(self):
        i18n.set_language("vi", MemorySettings())

        expected = {
            "Blur": "Blur",
            "Logo": "Logo",
            "Mask": "Mask",
            "Text": "Text",
            "Undo": "Undo",
            "Redo": "Redo",
            "Split": "Split",
            "Layers": "Layers",
            "Fit": "Fit",
            "Auto-Fit All Voice": "Khớp giọng đọc toàn bộ",
            "Revert All Freezes": "Hoàn tác toàn bộ",
            "↺ Revert All Freezes": "↺ Hoàn tác toàn bộ",
            "⚡ Auto-Fit All Voice": "⚡ Khớp giọng đọc toàn bộ",
            "Style": "Kiểu subtitle",
            "Style:": "Kiểu subtitle:",
        }
        for source, translation in expected.items():
            with self.subTest(source=source):
                self.assertEqual(i18n.t(source), translation)

    def test_language_persistence_and_invalid_fallback(self):
        settings = MemorySettings()

        self.assertEqual(i18n.set_language("vi-VN", settings), "vi")
        self.assertEqual(i18n.get_language(settings), "vi")
        self.assertEqual(i18n.normalize_language("fr"), "en")

    def test_english_is_default_and_missing_text_falls_back(self):
        settings = MemorySettings()

        self.assertEqual(i18n.get_language(settings), "en")
        i18n.set_language("en", settings)
        self.assertEqual(i18n.t("Start Export"), "Start Export")
        self.assertEqual(i18n.t("Uncatalogued text"), "Uncatalogued text")

    def test_language_items_keep_stable_codes(self):
        self.assertEqual(
            i18n.language_items(),
            (("English", "en"), ("Tiếng Việt", "vi")),
        )

    def test_localizer_accepts_non_widget_qt_windows(self):
        i18n.set_language("vi", MemorySettings())
        i18n.localize_widget_tree(QWindow())

    def test_localizer_does_not_translate_ai_prompt_body(self):
        prompt = QPlainTextEdit()
        source_prompt = "You are a subtitle translator. Preserve the exact SRT structure."
        prompt.setPlainText(source_prompt)

        i18n.set_language("vi", MemorySettings())
        i18n.localize_widget_tree(prompt)

        self.assertEqual(prompt.toPlainText(), source_prompt)

    def test_localizer_round_trips_labels_and_combo_values(self):
        host = QWidget()
        label = QLabel("Start Export", host)
        combo = QComboBox(host)
        combo.addItem("Max (source)", "source")

        i18n.set_language("vi", MemorySettings())
        i18n.localize_widget_tree(host)
        self.assertEqual(label.text(), "Bắt đầu xuất")
        self.assertEqual(i18n.current_source_text(combo), "Max (source)")

        i18n.set_language("en", MemorySettings())
        i18n.localize_widget_tree(host)
        self.assertEqual(label.text(), "Start Export")
        self.assertEqual(combo.currentText(), "Max (source)")

    def test_localizer_does_not_translate_user_entered_prompt_fields(self):
        host = QWidget()
        prompt = QLineEdit(host)
        prompt.setText("Start Export")

        i18n.set_language("vi", MemorySettings())
        i18n.localize_widget_tree(host)

    def test_pipeline_progress_step_labels_and_live_translation(self):
        settings = MemorySettings()
        i18n.set_language("vi", settings)

        self.assertEqual(i18n.t("Subtitle Processing (AI)"), "Xử lý phụ đề (AI)")
        self.assertEqual(i18n.t("Synthesizing AI Voiceover"), "Tạo giọng đọc AI (TTS)")
        self.assertEqual(i18n.t("Preparing Video Preview"), "Chuẩn bị xem trước video")
        self.assertEqual(
            i18n.t("Translating subtitles: 30/120 cues (25%)"),
            "Đang dịch phụ đề: 30/120 câu (25%)",
        )


if __name__ == "__main__":
    unittest.main()
