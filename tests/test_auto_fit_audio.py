import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from ui.main_window import VideoTranslatorGUI
from ui.controllers.preview_controller import PreviewController
from ui.utils.preview_audio import _PreviewAudioWorker

app = QApplication.instance() or QApplication([])


class TestAutoFitAudio(unittest.TestCase):
    def setUp(self):
        self.folder = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.original = self.folder / "original.wav"
        self.voice = self.folder / "voice.wav"
        self.sr = 16000
        sf.write(self.original, np.full(3 * self.sr, 0.2), self.sr, subtype="FLOAT")
        self.voice_pcm = np.zeros(5 * self.sr)
        self.voice_pcm[int(4.2 * self.sr):int(4.8 * self.sr)] = 0.3
        sf.write(self.voice, self.voice_pcm, self.sr, subtype="FLOAT")
        self.warps = [{"type": "slow", "time": 2.0, "media_start": 0.0,
                       "media_end": 2.0, "duration": 2.0, "speed": 0.5}]
        self.gui = MagicMock()
        gui = self.gui
        gui._preview_has_warps = False
        gui.video_time_warps = self.warps
        gui.media_player.duration.return_value = 3000
        gui.timeline.duration = 5000
        gui._audio_total_duration_ms.side_effect = lambda: VideoTranslatorGUI._audio_total_duration_ms(gui)
        gui._resolve_preview_voice_only_audio_path.return_value = str(self.voice)
        gui._resolve_preview_original_audio_path.return_value = str(self.original)
        gui.using_existing_audio_source.return_value = False
        gui._get_audio_track_volume.return_value = 100.0
        gui._is_audio_track_muted.return_value = False
        gui._tts_audio_track_state.return_value = {"volume": 100.0, "muted": False}
        gui._music_audio_tracks.return_value = []
        gui.get_project_temp_dir.return_value = str(self.folder)

    def test_audio_duration_includes_all_extensions(self):
        self.gui.video_time_warps = self.warps + [{"type": "freeze", "time": 2.5, "duration": 0.5}]
        self.assertEqual(self.gui._audio_total_duration_ms(), 5500)

    def test_rendered_preview_and_timeline_fallback_do_not_double_count_extensions(self):
        preview = self.folder / "preview.mp4"
        preview.touch()
        self.gui._preview_has_warps = True
        self.gui.last_preview_video_path = str(preview)
        self.gui.media_player._source_path = str(preview)
        self.gui.media_player.duration.return_value = 5000
        self.assertEqual(self.gui._audio_total_duration_ms(), 5000)
        self.gui.media_player.duration.return_value = 0
        self.assertEqual(self.gui._audio_total_duration_ms(), 5000)

    def test_native_preview_keeps_original_and_voice_after_old_video_end(self):
        worker = _PreviewAudioWorker()
        self.addCleanup(worker.close)
        end = self.gui._audio_total_duration_ms() / 1000.0
        worker.set_tracks([
            {"id": "A1", "path": str(self.original), "end": end, "is_original_video": True},
            {"id": "TS1", "path": str(self.voice), "end": end},
        ], self.warps)
        block = worker._read_mixed_block(int(4.5 * self.sr))
        np.testing.assert_allclose(block, 0.5, atol=1e-4)

    def test_native_reader_reloads_rebuilt_voice_at_same_path(self):
        worker = _PreviewAudioWorker()
        self.addCleanup(worker.close)
        sf.write(self.voice, np.zeros(3 * self.sr), self.sr, subtype="FLOAT")
        tracks = [{"id": "TS1", "path": str(self.voice), "end": 5.0}]
        worker.set_tracks(tracks)
        worker._read_mixed_block(int(4.5 * self.sr))
        sf.write(self.voice, self.voice_pcm, self.sr, subtype="FLOAT")
        worker.set_tracks(tracks)
        block = worker._read_mixed_block(int(4.5 * self.sr))
        np.testing.assert_allclose(block, 0.3, atol=1e-4)

    def test_export_keeps_original_and_voice_after_old_video_end(self):
        output = PreviewController(self.gui)._regenerate_mixed_audio_with_current_volumes()
        pcm, sr = sf.read(output)
        self.assertEqual(len(pcm), 5 * sr)
        np.testing.assert_allclose(pcm[int(4.5 * sr):int(4.51 * sr)], 0.5, atol=1e-4)

    def test_export_single_original_track_is_stretched_to_timeline_duration(self):
        self.gui._tts_audio_track_state.return_value = {"volume": 100.0, "muted": True}
        output = PreviewController(self.gui)._regenerate_mixed_audio_with_current_volumes()
        pcm, sr = sf.read(output)
        self.assertEqual(len(pcm), 5 * sr)
        np.testing.assert_allclose(pcm[int(4.5 * sr):int(4.51 * sr)], 0.2, atol=1e-4)

    def test_export_freeze_silences_original_then_resumes_at_new_tail(self):
        self.gui.video_time_warps = [{"type": "freeze", "time": 1.0, "duration": 2.0}]
        output = PreviewController(self.gui)._regenerate_mixed_audio_with_current_volumes()
        pcm, sr = sf.read(output)
        self.assertEqual(len(pcm), 5 * sr)
        np.testing.assert_allclose(pcm[int(1.5 * sr):int(1.51 * sr)], 0.0, atol=1e-4)
        np.testing.assert_allclose(pcm[int(4.5 * sr):int(4.51 * sr)], 0.5, atol=1e-4)


if __name__ == "__main__":
    unittest.main()
