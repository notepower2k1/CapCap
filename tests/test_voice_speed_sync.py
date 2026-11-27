import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "ui"):
    sys.path.insert(0, str(folder))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.core.models import Segment
from app.services.project_service import ProjectService
from app.services.reading_rhythm import READING_RHYTHM, fit_reading_rhythm, shift_source_subtitles
from app.workflows.voice_workflow import VoiceWorkflow
from ui.main_window import VideoTranslatorGUI


class TestVoiceSpeedSync(unittest.TestCase):
    def test_switching_tts_provider_keeps_each_cached_waveform(self):
        with tempfile.TemporaryDirectory() as tmp_dir, patch.dict(os.environ, {"CAPCUT_TTS_BATCH_SIZE": "1"}):
            workflow = VoiceWorkflow(tmp_dir)
            capcut = "capcut:example"
            piper = "piper:example"
            written_voices = []

            def synthesize(*, wav_path, voice, **kwargs):
                written_voices.append(voice)
                with wave.open(wav_path, "wb") as wav:
                    wav.setparams((1, 2, 16000, 0, "NONE", ""))
                    wav.writeframes((b"\x01\x00" if voice == capcut else b"\x02\x00") * 16000)
                return wav_path

            args = dict(segments=[{"start": 0., "end": 1., "text": "Hello"}], tmp_dir=tmp_dir, log=False)
            with patch.object(workflow.engine_runtime, "synthesize_segment", side_effect=synthesize):
                capcut_path, = workflow._synthesize_segment_wavs(voice_name=capcut, **args)
                piper_path, = workflow._synthesize_segment_wavs(voice_name=piper, **args)
                reused, = workflow._synthesize_segment_wavs(voice_name=capcut, **args)
            self.assertNotEqual(capcut_path, piper_path)
            self.assertEqual(reused, capcut_path)
            self.assertEqual(written_voices, [capcut, piper])
            with wave.open(reused, "rb") as wav:
                self.assertEqual(wav.readframes(1), b"\x01\x00")

    def test_timeline_priority_cuts_after_applying_manual_speed(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            wav_path = str(Path(tmp_dir) / "speech.wav")
            samples = (6000 * np.sin(2 * np.pi * 220 * np.arange(64000) / 16000)).astype("<i2")
            with wave.open(wav_path, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", ""))
                wav.writeframes(samples.tobytes())
            rows = [{"start": 0., "end": 1., "voice_speed": .8}, {"start": 2., "end": 3.}]
            result = workflow._apply_safe_timing_polish(
                segments=rows, wavs=[wav_path, ""], tmp_dir=tmp_dir,
                voice_speed=1.0, sync_mode="Timeline Priority",
            )
            self.assertAlmostEqual(workflow._probe_wav_duration_seconds(result[0]), 2.0, delta=.002)

    def test_new_audio_refreshes_timeline_even_if_the_cue_did_not_change(self):
        gui = MagicMock()
        row = {"start": 0., "end": 2., "text": "Hello", "tts_text": "Hello",
               "_audio_end": 4., "_wav_path": "capcut.wav",
               "metadata": {"_audio_end": 4., "_wav_path": "capcut.wav"}}
        gui.current_translated_segments = [row]
        self.assertTrue(VideoTranslatorGUI._apply_generated_tts_texts(gui, [
            {"start": 0., "end": 2., "tts_text": "Hello", "_audio_end": 1.5, "_wav_path": "piper.wav"},
        ]))
        saved = Segment.from_dict(row).to_subtitle_dict()
        self.assertEqual((saved["_audio_end"], saved["_wav_path"]), (1.5, "piper.wav"))

    def test_reading_rhythm_keeps_requested_speed_on_overflow(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            wav_path = str(Path(tmp_dir) / "speech.wav")
            samples = (6000 * np.sin(2 * np.pi * 220 * np.arange(64000) / 16000)).astype("<i2")
            with wave.open(wav_path, "wb") as wav:
                wav.setparams((1, 2, 16000, 0, "NONE", ""))
                wav.writeframes(samples.tobytes())
            segment = {"start": 0., "end": 2., "voice_speed": 1.0}
            result = workflow._apply_safe_timing_polish(
                segments=[segment], wavs=[wav_path], tmp_dir=tmp_dir,
                voice_speed=1.1, sync_mode=READING_RHYTHM,
            )
            self.assertAlmostEqual(workflow._probe_wav_duration_seconds(result[0]), 4 / 1.1, delta=.04)
            self.assertFalse(segment.get("_auto_fit_applied"))

    def test_reading_rhythm_uses_gap_then_caps_extension_and_shifts_grouped_audio(self):
        rows = [{"start": 0., "end": 1., "tts_group_id": "a"},
                {"start": 1., "end": 2., "tts_group_id": "a"},
                {"start": 2.2, "end": 4.}]
        voices = [{"start": 0., "end": 2., "_audio_end": 3.1, "tts_group_id": "a"},
                  {"start": 2.2, "end": 4., "_audio_end": 3.5}]
        off = fit_reading_rhythm(rows, voices, 10., [])
        self.assertEqual(off.shifts, [])
        self.assertEqual(off.subtitles[1]["end"], 2.2)
        self.assertEqual(off.unresolved, 1)
        on = fit_reading_rhythm(rows, voices, 10., [], auto_extend=True)
        self.assertEqual(on.shifts, [(2., .333)])
        self.assertGreaterEqual(on.warps[0]["speed"], .75)
        self.assertEqual(on.voice_segments[1]["start"], 2.533)
        self.assertEqual(on.subtitles[1]["end"], 2.533)
        self.assertEqual(on.unresolved, 1)
        original = [{"start": row["start"], "end": row["end"]} for row in rows]
        shift_source_subtitles(original, on)
        self.assertEqual(original[1]["end"], 2.333)
        self.assertEqual(original[2]["start"], 2.533)
        repeated = fit_reading_rhythm(on.subtitles, on.voice_segments, 10., on.warps, auto_extend=True)
        self.assertEqual(repeated.shifts, [])

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
