import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
for directory in (ROOT,ROOT/'app',ROOT/'ui'):
    sys.path.insert(0,str(directory))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QDialog,QGridLayout,QMessageBox
from services.project_service import ProjectService
from services.processing_queue import ProcessingQueue
from services.video_data_cleanup import clean_generated_video_data
from services.project_work_mode import project_work_mode
from views import launcher
from views.processing_queue import QueuePanel


class VideoDataCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_clean_keeps_log_and_source_and_removes_caches(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'source.mp4';video.write_bytes(b'source')
            service=ProjectService(folder);service.ensure_project(str(video))
            log=root/'temp'/'capcap_runtime.log';log.parent.mkdir();log.write_text('session')
            (log.parent/'preview.wav').write_bytes(b'preview')
            with log.open('a'):
                result=clean_generated_video_data(folder)
            self.assertFalse(result.errors)
            self.assertFalse(result.remaining_project_ids)
            self.assertEqual(log.read_text(),'session')
            self.assertEqual(video.read_bytes(),b'source')
            self.assertFalse((log.parent/'preview.wav').exists())

    @unittest.skipUnless(os.name=='nt','Windows file sharing lock')
    def test_launcher_retains_locked_project_then_clears_history_and_queue_on_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);video=root/'source.mp4';video.write_bytes(b'source')
            service=ProjectService(folder);state=service.ensure_project(str(video))
            state.set_setting('ui_work_mode','performance');service.save_project(state)
            wav=Path(state.project_root)/'audio'/'locked.wav';wav.write_bytes(b'voice')
            window=QDialog();window.grid=QGridLayout(window)
            window._load_recent=lambda:None
            window._queue_panel=QueuePanel(folder,window)
            panel=window._queue_panel;panel.store.add([str(video)]);panel.refresh()
            with patch.object(launcher,'workspace_root',return_value=folder), \
                 patch.object(QMessageBox,'exec',return_value=QMessageBox.Yes):
                launcher._save_recent_projects(None,[dict(video_path=str(video))])
                with wav.open('rb'):
                    launcher.LauncherWindow._on_clean_video_data(window)
                self.assertTrue(Path(state.project_root).is_dir())
                self.assertEqual(len(launcher._load_recent_projects()),1)
                self.assertEqual(len(ProcessingQueue(folder).jobs),1)
                self.assertEqual(panel.table.rowCount(),1)
                launcher.LauncherWindow._on_clean_video_data(window)
                self.assertFalse(launcher._load_recent_projects())
                self.assertFalse(ProcessingQueue(folder).jobs)
                self.assertEqual(panel.table.rowCount(),0)
                self.assertIsNone(project_work_mode(folder,str(video)))
                self.assertFalse(panel.store.add([str(video)]))
                self.assertEqual(len(panel.store.jobs),1)
                self.assertTrue(video.is_file())
            window.deleteLater()
            self.app.processEvents()


if __name__=='__main__':unittest.main()
