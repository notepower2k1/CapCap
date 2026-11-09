import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "ui"):
    sys.path.insert(0, str(folder))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.models import Segment
from app.services.project_service import ProjectService
from app.workflows.voice_workflow import VoiceWorkflow
from ui.main_window import VideoTranslatorGUI


class TestVoiceSpeedSync(unittest.TestCase):
    def test_editor_srt_refresh_keeps_manual_and_automatic_speed(self):
        for keep_timeline in (True, False):
            for automatic in (True, False):
                with self.subTest(keep_timeline=keep_timeline, automatic=automatic):
                    gui = MagicMock()
                    gui.keep_timeline_cb.isChecked.return_value = keep_timeline
                    gui.extract_subtitle_text_entries.return_value = ["Xin chào"]
                    gui.parse_srt_to_segments.return_value = [{"start": 0., "end": 2., "text": "Xin chào"}]
                    base = [{"start": 0., "end": 2., "text": "Xin chào", "voice_speed": 1.232,
                             "_auto_fit_applied": automatic, "_wav_path": "processed.wav"}]
                    result = VideoTranslatorGUI._segments_from_editor_text(gui, "1\n00:00:00,000 --> 00:00:02,000\nXin chào", base)
                    self.assertEqual(result[0]["voice_speed"], 1.232)
                    self.assertEqual(result[0]["_auto_fit_applied"], automatic)

    def test_project_load_recovers_speed_only_from_the_same_processed_audio(self):
        import json
        from types import SimpleNamespace
        from services.gui_project_bridge import GUIProjectBridge
        for manual_speed in (None, 1.27):
            with tempfile.TemporaryDirectory() as folder:
                translation_path = Path(folder) / "translation.json"
                translation_path.write_text("[]")
                voice_path = Path(folder) / "voice.json"
                voice_path.write_text(json.dumps([
                    {"_wav_path": "same.wav", "voice_speed": 1.232, "_auto_fit_applied": True},
                    {"_wav_path": "old.wav", "voice_speed": 1.5, "_auto_fit_applied": True},
                ]))
                models = [Segment.from_dict({"text": "Xin chào", "start": 0, "end": 2, "_wav_path": "same.wav"}),
                          Segment.from_dict({"text": "Other", "start": 2, "end": 4, "_wav_path": "new.wav"})]
                if manual_speed is not None:
                    models[0].metadata.update(voice_speed=manual_speed, _auto_fit_applied=False)
                service = MagicMock()
                service.load_segment_artifact.return_value = models
                state = SimpleNamespace(artifacts={"translation_final": str(translation_path), "voice_segments": str(voice_path)})
                context = GUIProjectBridge(service).load_context(state)
                recovered, untouched = context["current_translated_segments"]
                self.assertEqual(recovered["voice_speed"], manual_speed or 1.232)
                self.assertEqual(recovered["_auto_fit_applied"], manual_speed is None)
                self.assertNotIn("voice_speed", untouched)

    def test_automatic_speed_matches_audio_and_reaches_ui_and_saved_segment(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            for mode in ("Smart", "Force Fit", "Off", "Timeline Priority"):
                for target, duration in ((3.0, 3.3), (2.0, 4.0)):
                    with self.subTest(mode=mode, target=target):
                        wav_path = os.path.join(tmp_dir, "base.wav")
                        samples = (6000 * np.sin(2 * np.pi * 220 * np.arange(int(duration * 16000)) / 16000)).astype("<i2")
                        with wave.open(wav_path, "wb") as wav:
                            wav.setnchannels(1)
                            wav.setsampwidth(2)
                            wav.setframerate(16000)
                            wav.writeframes(samples.tobytes())
                        segment = {"text": "Xin chào", "tts_text": "Xin chào", "start": 0.0, "end": target, "voice_speed": 1.0}
                        result = workflow._apply_safe_timing_polish(
                            segments=[segment], wavs=[wav_path], tmp_dir=tmp_dir,
                            voice_speed=1.0, sync_mode=mode,
                        )
                        actual_duration = workflow._probe_wav_duration_seconds(result[0])
                        self.assertGreater(segment["voice_speed"], 1.0)
                        self.assertTrue(segment["_auto_fit_applied"])
                        if mode != "Timeline Priority":
                            self.assertAlmostEqual(segment["voice_speed"], duration / actual_duration, delta=0.02)
                        gui = MagicMock()
                        gui.current_translated_segments = [{
                            "text": "Xin chào", "tts_text": "Xin chào", "start": 0.0, "end": target, "voice_speed": 1.0,
                        }]
                        self.assertTrue(VideoTranslatorGUI._apply_generated_tts_texts(gui, [segment]))
                        saved = Segment.from_dict(gui.current_translated_segments[0])
                        restored = Segment.from_dict(saved.to_dict()).to_subtitle_dict()
                        self.assertEqual(restored["voice_speed"], segment["voice_speed"])
                        self.assertTrue(restored["_auto_fit_applied"])
                        prepared = workflow._prepare_segments_for_tts([restored], log=False)
                        regenerated = workflow._apply_safe_timing_polish(
                            segments=prepared, wavs=[wav_path], tmp_dir=tmp_dir,
                            voice_speed=1.0, sync_mode=mode,
                        )
                        self.assertAlmostEqual(
                            workflow._probe_wav_duration_seconds(regenerated[0]), actual_duration, delta=0.02,
                        )

    def test_automatic_result_is_recomputed_on_regeneration_but_manual_speed_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            automatic = {"text": "Xin chào", "start": 0.0, "end": 2.0, "voice_speed": 1.234, "_auto_fit_applied": True}
            prepared = workflow._prepare_segments_for_tts([automatic], log=False)[0]
            self.assertEqual(float(prepared.get("voice_speed", 1.0)), 1.0)
            self.assertFalse(prepared.get("_auto_fit_applied", False))
            self.assertEqual(automatic["voice_speed"], 1.234)

            gui = MagicMock()
            gui._syncing_segment_editor = False
            gui.current_translated_segments = [automatic]
            gui.current_segments = []
            VideoTranslatorGUI.on_segment_voice_speed_changed(gui, 0, 1.27)
            self.assertEqual(automatic["voice_speed"], 1.27)
            self.assertFalse(automatic.get("_auto_fit_applied", False))
            manual = workflow._prepare_segments_for_tts([automatic], log=False)[0]
            self.assertEqual(manual["voice_speed"], 1.27)

    def test_small_speed_change_refreshes_ui_and_clears_stale_automatic_state(self):
        gui = MagicMock()
        gui.current_translated_segments = [{"text": "Xin chào", "tts_text": "Xin chào", "start": 0.0, "end": 2.0, "voice_speed": 1.0}]
        self.assertTrue(VideoTranslatorGUI._apply_generated_tts_texts(gui, [{
            "text": "Xin chào", "tts_text": "Xin chào", "start": 0.0, "end": 2.0,
            "voice_speed": 1.03, "_auto_fit_applied": True,
        }]))
        self.assertEqual(gui.current_translated_segments[0]["voice_speed"], 1.03)
        self.assertTrue(VideoTranslatorGUI._apply_generated_tts_texts(gui, [{
            "text": "Xin chào", "tts_text": "Xin chào", "start": 0.0, "end": 2.0, "voice_speed": 1.03,
        }]))
        self.assertFalse(gui.current_translated_segments[0].get("_auto_fit_applied", False))

    def test_grouped_voice_keeps_speed_and_automatic_state(self):
        gui = MagicMock()
        gui.current_translated_segments = [
            {"text": "Xin", "start": 0.0, "end": 1.0, "tts_group_id": "group", "voice_speed": 1.23, "_auto_fit_applied": True},
            {"text": "chào", "start": 1.0, "end": 2.0, "tts_group_id": "group", "voice_speed": 1.23, "_auto_fit_applied": True},
        ]
        gui._apply_speaker_voice_assignments.side_effect = lambda segments: segments
        grouped = VideoTranslatorGUI._get_voiceover_segments(gui)
        self.assertEqual(len(grouped), 1)
        self.assertEqual(grouped[0]["voice_speed"], 1.23)
        self.assertTrue(grouped[0]["_auto_fit_applied"])

    def test_manual_speed_changes_invalidate_voice_cache_but_automatic_results_do_not(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ProjectService(tmp_dir)
            segment = {"text": "Xin chào", "start": 0.0, "end": 2.0}
            original = service.build_voice_signature([segment])
            segment.update(voice_speed=1.03, _auto_fit_applied=True)
            self.assertEqual(service.build_voice_signature([segment]), original)
            segment["_auto_fit_applied"] = False
            self.assertNotEqual(service.build_voice_signature([segment]), original)


if __name__ == "__main__":
    unittest.main()
