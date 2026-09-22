import os
import sys
import unittest
from unittest.mock import MagicMock, patch

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from worker_adapters.processing_workers import AlternateRangeTranscriptionWorker
import ocr_processor


class TestAlternateRangeTranscriptionWorker(unittest.TestCase):
    def test_init_parameters(self):
        worker = AlternateRangeTranscriptionWorker(
            video_path="test.mp4",
            start=10.5,
            end=25.0,
            engine_name="sensevoice",
            model_path="base",
            language="zh",
            ocr_region="bottom",
            ocr_fps=1.0,
            ocr_backend="winocr",
        )
        self.assertEqual(worker.video_path, "test.mp4")
        self.assertEqual(worker.start_time, 10.5)
        self.assertEqual(worker.end_time, 25.0)
        self.assertEqual(worker.engine_name, "sensevoice")
        self.assertEqual(worker.language, "zh")
        self.assertEqual(worker.ocr_backend, "winocr")
        self.assertEqual(worker.ocr_region, "bottom")
        self.assertEqual(worker.ocr_fps, 1.0)

    @patch("worker_adapters.processing_workers.EngineRuntime")
    @patch("worker_adapters.processing_workers.subprocess.run")
    def test_run_sensevoice(self, mock_subprocess, mock_engine_cls):
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.transcribe_audio_sensevoice.return_value = [
            {"start": 1.0, "end": 3.5, "text": "你好"},
            {"start": 4.0, "end": 6.0, "text": "世界"},
        ]

        worker = AlternateRangeTranscriptionWorker(
            video_path="test.mp4",
            start=10.0,
            end=20.0,
            engine_name="sensevoice",
            language="zh",
        )

        results = []
        worker.completed.connect(lambda segs, summary: results.append((segs, summary)))
        worker.run()

        mock_subprocess.assert_called_once()
        cmd = mock_subprocess.call_args[0][0]
        self.assertIn("-ss", cmd)
        self.assertIn("10.0", cmd)
        self.assertIn("-t", cmd)
        self.assertIn("10.0", cmd)

        mock_engine.transcribe_audio_sensevoice.assert_called_once()
        self.assertEqual(len(results), 1)
        segs, summary = results[0]
        self.assertEqual(len(segs), 2)
        # Check that timestamps were offset by start_time (10.0)
        self.assertAlmostEqual(segs[0]["start"], 11.0)
        self.assertAlmostEqual(segs[0]["end"], 13.5)
        self.assertAlmostEqual(segs[1]["start"], 14.0)
        self.assertAlmostEqual(segs[1]["end"], 16.0)

    @patch("worker_adapters.processing_workers.EngineRuntime")
    def test_run_ocr_with_backend(self, mock_engine_cls):
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.transcribe_video_ocr.return_value = [
            {"start": 5.0, "end": 8.0, "text": "Hello OCR"},
        ]

        worker = AlternateRangeTranscriptionWorker(
            video_path="test.mp4",
            start=5.0,
            end=15.0,
            engine_name="ocr",
            ocr_backend="winocr",
            ocr_region="bottom",
            ocr_fps=2.0,
            language="en",
        )

        results = []
        worker.completed.connect(lambda segs, summary: results.append((segs, summary)))
        worker.run()

        mock_engine.transcribe_video_ocr.assert_called_once()
        _, kwargs = mock_engine.transcribe_video_ocr.call_args
        self.assertEqual(kwargs.get("ocr_backend"), "winocr")
        self.assertEqual(kwargs.get("language"), "en")
        self.assertEqual(kwargs.get("region"), "bottom")
        self.assertEqual(kwargs.get("fps"), 2.0)
        self.assertEqual(kwargs.get("start_seconds"), 5.0)
        self.assertEqual(kwargs.get("end_seconds"), 15.0)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0][0][0]["text"], "Hello OCR")

    @patch("worker_adapters.processing_workers.EngineRuntime")
    @patch("worker_adapters.processing_workers.subprocess.run")
    def test_run_capcut(self, mock_subprocess, mock_engine_cls):
        mock_engine = MagicMock()
        mock_engine_cls.return_value = mock_engine
        mock_engine.transcribe_audio_capcut.return_value = [
            {"start": 0.5, "end": 2.0, "text": "CapCut text"},
        ]

        worker = AlternateRangeTranscriptionWorker(
            video_path="test.mp4",
            start=12.0,
            end=18.0,
            engine_name="capcut",
            language="vi",
        )

        results = []
        worker.completed.connect(lambda segs, summary: results.append((segs, summary)))
        worker.run()

        mock_subprocess.assert_called_once()
        mock_engine.transcribe_audio_capcut.assert_called_once()
        self.assertEqual(len(results), 1)
        # Check offset by 12.0
        self.assertAlmostEqual(results[0][0][0]["start"], 12.5)
        self.assertAlmostEqual(results[0][0][0]["end"], 14.0)


class TestOcrProcessorBackendSelection(unittest.TestCase):
    @patch("ocr_processor.subprocess.run")
    @patch("ocr_processor._open_video")
    @patch("ocr_processor._load_ocr_engine")
    def test_transcribe_video_ocr_passes_backend(self, mock_load, mock_open, mock_subp):
        mock_subp.return_value = MagicMock(stderr="Duration: 00:00:10.00, start: 0.000000")
        mock_cap_obj = MagicMock()
        mock_cap_obj.get.return_value = 25.0
        mock_cap_obj.isOpened.return_value = True
        mock_cap_obj.read.return_value = (False, None)
        mock_open.return_value = mock_cap_obj
        mock_load.return_value = MagicMock()

        # Test with ocr_backend
        ocr_processor.transcribe_video_ocr("dummy.mp4", ocr_backend="winocr", language="zh", fps=1.0)
        mock_load.assert_called_with(backend="winocr", lang="zh")

        # Test with backend alias
        ocr_processor.transcribe_video_ocr("dummy.mp4", backend="rapidocr", language="en", fps=1.0)
        mock_load.assert_called_with(backend="rapidocr", lang="en")


class TestRangeTranscriptionDialog(unittest.TestCase):
    def test_dialog_engine_options(self):
        from main_window import VideoTranslatorGUI
        from PySide6.QtWidgets import QDialog, QWidget

        class DummyGUI(QWidget):
            def __init__(self):
                super().__init__()
                self.current_segments = []
                self.settings = MagicMock()
                self.settings.value.return_value = "rapidocr"
            def get_transcription_engine(self):
                return "sensevoice"
            def get_source_language_code(self):
                return "vi"
            def get_whisper_model_name(self):
                return "small"

        gui = DummyGUI()

        with patch.object(QDialog, "exec", return_value=QDialog.Accepted):
            cfg = VideoTranslatorGUI._show_range_transcription_dialog(gui, (5.0, 15.0))

        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["start"], 5.0)
        self.assertEqual(cfg["end"], 15.0)
        self.assertEqual(cfg["engine"], "sensevoice")
        self.assertIn("ocr_backend", cfg)
        self.assertIn(cfg["ocr_backend"], ("rapidocr", "winocr"))



if __name__ == "__main__":
    unittest.main()

