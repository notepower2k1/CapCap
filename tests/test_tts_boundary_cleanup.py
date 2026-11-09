import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ui"), str(ROOT / "app"), str(ROOT)]
from app.audio_mixer import trim_tts_boundary_silence
from app.workflows.voice_workflow import VoiceWorkflow


class BoundaryCleanupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sr = 24000

    def silence(self, duration):
        return np.zeros(round(duration * self.sr))

    def tone(self, duration, amplitude=.25):
        return amplitude * np.sin(2 * np.pi * 220 * np.arange(round(duration * self.sr)) / self.sr)

    def write(self, samples, name="source.wav", subtype="PCM_16"):
        path = self.root / name
        sf.write(path, samples, self.sr, subtype=subtype)
        return str(path)

    def trim(self, source):
        return trim_tts_boundary_silence(input_wav_path=source, output_wav_path=str(self.root / "trimmed.wav"))

    def test_stereo_quiet_edges_internal_pause_and_sample_format_are_preserved(self):
        spoken = np.concatenate([self.tone(.2, .003), self.tone(.4), self.silence(.8), self.tone(.3), self.tone(.2, .003)])
        raw = np.concatenate([self.silence(.7), spoken, self.silence(.9)])
        source = self.write(np.column_stack((raw, -raw)), subtype="PCM_24")
        original = Path(source).read_bytes()
        output = self.trim(source)
        self.assertNotEqual(output, source)
        result, sr = sf.read(output, always_2d=True)
        start = round(.58 * self.sr)
        end = round((.7 + len(spoken) / self.sr + .12) * self.sr)
        expected, _ = sf.read(source, always_2d=True)
        np.testing.assert_array_equal(result, expected[start:end])
        self.assertEqual(sr, self.sr)
        self.assertEqual(sf.info(output).subtype, "PCM_24")
        self.assertEqual(Path(source).read_bytes(), original)
        self.assertEqual(self.trim(output), output)  # idempotent, no repeated edge erosion

    def test_short_natural_pauses_and_uncertain_whispers_are_untouched(self):
        for name, samples in (
            ("short", np.concatenate([self.silence(.2), self.tone(.6), self.silence(.25)])),
            ("quiet", np.concatenate([self.silence(.8), self.tone(.5, .004), self.silence(.8)])),
            ("silent", self.silence(2)),
        ):
            source = self.write(samples, name + ".wav")
            self.assertEqual(self.trim(source), source)

    def test_small_signal_in_last_partial_block_is_not_discarded(self):
        samples = np.concatenate([self.tone(.5), self.silence(.6), np.array([.003, -.003, .003])])
        source = self.write(samples)
        self.assertEqual(self.trim(source), source)

    def test_bad_input_and_same_destination_leave_source_untouched(self):
        source = self.root / "unreadable.wav"
        source.write_bytes(b"invalid wav")
        self.assertEqual(self.trim(str(source)), str(source))
        self.assertEqual(trim_tts_boundary_silence(input_wav_path=str(source), output_wav_path=str(source)), str(source))
        self.assertEqual(source.read_bytes(), b"invalid wav")

    def test_all_modes_cleanup_before_fit_and_clear_stale_audio_end(self):
        source = self.write(np.concatenate([self.silence(.8), self.tone(.5), self.silence(.3), self.tone(.2), self.silence(.9)]))
        for mode in ("off", "smart", "force", "Timeline Priority"):
            for manual in (1., 1.2):
                with self.subTest(mode=mode, manual=manual):
                    workflow = VoiceWorkflow(str(self.root))
                    segment = {"text": "テスト", "start": 3., "end": 4.5, "voice_speed": manual,
                               "_audio_end": 8., "metadata": {"_audio_end": 8.}}
                    with patch("requests.sessions.Session.request", side_effect=AssertionError("No API calls")):
                        paths = workflow._apply_safe_timing_polish(segments=[segment], wavs=[source], tmp_dir=str(self.root),
                                                                 voice_speed=1., sync_mode=mode)
                        workflow._extend_segment_ends_to_audio(segments=[segment], wavs=paths)
                    self.assertNotEqual(paths[0], source)
                    duration = sf.info(paths[0]).duration
                    self.assertLess(duration, 1.4)
                    self.assertAlmostEqual(segment["_audio_end"], 3 + duration, places=4)
                    self.assertEqual(segment["metadata"]["_audio_end"], segment["_audio_end"])
                    self.assertLess(segment["_audio_end"], segment["end"])
                    self.assertAlmostEqual(segment["pre_speed_ratio"], 1.24 / 1.5, delta=.01)

    def test_full_voice_workflow_keeps_raw_cache_and_builds_cleaned_track(self):
        source = self.write(np.concatenate([self.silence(.8), self.tone(.5), self.silence(.9)]))
        before = hashlib.sha256(Path(source).read_bytes()).digest()
        workflow = VoiceWorkflow(str(self.root))
        segment = {"text": "テスト", "start": .3, "end": 1.8, "_audio_end": 9., "metadata": {"_audio_end": 9.}}
        with patch.object(workflow, "_synthesize_segment_wavs", return_value=[source]), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("No API calls")):
            result = workflow.run(segments=[segment], output_dir=str(self.root / "out"),
                                  project_temp_dir=str(self.root / "processing"), timing_sync_mode="off", target_language="ja")
        current = result["segments"][0]
        self.assertNotEqual(current["_wav_path"], source)
        duration = sf.info(current["_wav_path"]).duration
        self.assertAlmostEqual(current["_audio_end"], .3 + duration, places=5)
        self.assertEqual(current["metadata"]["_wav_path"], current["_wav_path"])
        self.assertEqual(hashlib.sha256(Path(source).read_bytes()).digest(), before)
        voice, sr = sf.read(result["voice_track"])
        self.assertGreater(np.max(np.abs(voice[round(.3 * sr):round((.3 + duration) * sr)])), .1)
        from app.utils.voice_preview_utils import load_manifest
        self.assertEqual(load_manifest(str(self.root / "processing"))["segments"]["0"]["wav_path"], source)

    def test_cleanup_version_invalidates_only_derived_track_signature(self):
        from app.services.project_service import ProjectService
        from app.utils.voice_preview_utils import segment_cache_key
        service = ProjectService(str(self.root))
        segment = {"text": "テスト", "start": 0, "end": 1}
        key_args = dict(text=segment["text"], voice_name="ngochuyen", provider_speed=1., normalizer_signature="")
        before = segment_cache_key(**key_args)
        with patch("app.tts_audio_policy.BOUNDARY_TRIM_VERSION", 0):
            old = service.build_voice_signature([segment])
        self.assertNotEqual(old, service.build_voice_signature([segment]))
        self.assertEqual(before, segment_cache_key(**key_args))

    def test_single_cue_regeneration_returns_cleaned_audio_and_keeps_raw_manifest(self):
        from app.services.engine_runtime import EngineRuntime
        from ui.worker_adapters.processing_workers import SegmentAudioPreviewWorker
        from app.utils.voice_preview_utils import load_manifest
        engine = EngineRuntime()
        raw_hashes = {}
        def fake_synthesize(**kwargs):
            path = Path(kwargs["wav_path"])
            sf.write(path, np.concatenate([self.silence(.8), self.tone(.5), self.silence(.9)]), self.sr)
            raw_hashes[str(path)] = hashlib.sha256(path.read_bytes()).digest()
            return str(path)
        engine.synthesize_segment = fake_synthesize
        results = []
        with patch("ui.worker_adapters.processing_workers.EngineRuntime", return_value=engine), \
             patch("requests.sessions.Session.request", side_effect=AssertionError("No API calls")):
            worker = SegmentAudioPreviewWorker(str(self.root), 0, "テスト", "ngochuyen", 1.,
                                              temp_dir=str(self.root / "preview"), cache_temp_dir=str(self.root / "cache"),
                                              target_language="ja")
            worker.finished.connect(lambda *args: results.append(args))
            worker.run()
        self.assertTrue(results)
        index, output, error = results[0]
        self.assertFalse(error, error)
        self.assertEqual(index, 0)
        self.assertLess(sf.info(output).duration, 1.)
        raw = load_manifest(str(self.root / "cache"))["segments"]["0"]["wav_path"]
        self.assertNotEqual(raw, output)
        self.assertEqual(hashlib.sha256(Path(raw).read_bytes()).digest(), raw_hashes[raw])


if __name__ == "__main__":
    unittest.main()
