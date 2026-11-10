"""Exercise the real settings dialog using the existing isolated Qt host."""

import os
import unittest

import test_asr_filter_settings as settings_harness
from PySide6.QtWidgets import QApplication, QComboBox, QPushButton
from ui.i18n import t


class TestBrowserParallelSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings_harness.TestASRFilterSettings.setUpClass()

    def setUp(self):
        self.host = settings_harness.TestASRFilterSettings()
        self.host.setUp()

    def tearDown(self):
        self.host.tearDown()

    def test_parallel_selection_is_saved_and_restored(self):
        for worker_count in ("2", "3", "4"):
            with self.subTest(workers=worker_count):
                os.environ["CAPCAP_BROWSER_AI_WORKERS"] = "1"
                def enable_parallel():
                    dialog = QApplication.activeModalWidget()
                    provider = next(combo for combo in dialog.findChildren(QComboBox) if combo.findData("browser_ai") >= 0)
                    provider.setCurrentIndex(provider.findData("browser_ai"))
                    workers = dialog.findChild(QComboBox, "browserAIWorkersComboBox")
                    self.assertEqual([workers.itemData(i) for i in range(workers.count())], ["1", "2", "3", "4"])
                    self.assertEqual(workers.currentData(), "1")
                    workers.setCurrentIndex(workers.findData(worker_count))
                    self.assertEqual(os.environ["CAPCAP_BROWSER_AI_WORKERS"], worker_count)
                    next(button for button in dialog.findChildren(QPushButton) if button.text() == t("Save")).click()

                self.host._open_dialog(enable_parallel)
                self.assertEqual(self.host.settings.value("browser_ai_workers"), worker_count)
                self.assertEqual(os.environ["CAPCAP_BROWSER_AI_WORKERS"], worker_count)
                # Reopen with only the saved setting (as after restarting the app).
                os.environ.pop("CAPCAP_BROWSER_AI_WORKERS", None)

                def check_restored():
                    dialog = QApplication.activeModalWidget()
                    self.assertEqual(dialog.findChild(QComboBox, "browserAIWorkersComboBox").currentData(), worker_count)
                    dialog.reject()

                self.host._open_dialog(check_restored)



if __name__ == "__main__":
    unittest.main()
