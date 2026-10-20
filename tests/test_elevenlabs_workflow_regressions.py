from __future__ import annotations

import base64
import hashlib
import math
import os
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "ui"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from app.engines.remote_tts_adapter import RemoteTTSAdapter
from app.services.engine_runtime import EngineRuntime
from app.services.project_service import ProjectService
from app.services.workflow_runtime import WorkflowRuntime
from app.utils.voice_preview_utils import load_manifest
from app.workflows.voice_workflow import VoiceWorkflow
from ui.worker_adapters.processing_workers import (
    SegmentAudioPreviewWorker,
    VoiceOverWorker,
    VoiceSamplePreviewWorker,
)


def _write_wav(path: str, frequency: int = 240) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    samples = bytearray()
    for idx in range(16000):
        value = int(5000 * math.sin(2 * math.pi * frequency * idx / 16000))
        samples.extend(int(value).to_bytes(2, byteorder="little", signed=True))
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(bytes(samples))
    return path


def _tone_for(*parts: str) -> int:
    value = "|".join(parts)
    return 180 + sum(value.encode("utf-8")) % 500


def _mock_workflow_engine(workflow: VoiceWorkflow, synthesize, voice_track_path: str | None = None):
    """Patch the already-created runtime instance so tests cannot reach external APIs."""
    workflow.engine_runtime.synthesize_segment = MagicMock(side_effect=synthesize)
    if voice_track_path is not None:
        workflow.engine_runtime.build_voice_track = MagicMock(return_value=voice_track_path)


class TestElevenLabsWorkflowRegressions(unittest.TestCase):
    def _run(self, workflow: VoiceWorkflow, output_dir: str, cache_dir: str, *, target_language="en"):
        return workflow.run(
            segments=[{"text": "José went to the café", "start": 0.0, "end": 1.0}],
            output_dir=output_dir,
            project_temp_dir=cache_dir,
            voice_name="elevenlabs:test-voice",
            voice_speed=1.0,
            target_language=target_language,
        )

    def test_model_changes_use_distinct_files_and_round_trip_to_original_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            output_dir = os.path.join(tmp_dir, "output")
            cache_dir = os.path.join(tmp_dir, "cache")
            calls = []

            def synthesize(**kwargs):
                model_id = kwargs["model_id"]
                language_code = kwargs["language_code"]
                calls.append((model_id, language_code))
                return _write_wav(kwargs["wav_path"], _tone_for(model_id, language_code))

            _mock_workflow_engine(workflow, synthesize, os.path.join(tmp_dir, "voice.wav"))

            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_flash_v2_5", "ELEVENLABS_TTS_WORKERS": "1"}):
                first = self._run(workflow, output_dir, cache_dir)
                first_path = first["segments"][0]["_wav_path"]
                first_bytes = Path(first_path).read_bytes()

                os.environ["ELEVENLABS_MODEL"] = "eleven_v3"
                second = self._run(workflow, output_dir, cache_dir)
                second_path = second["segments"][0]["_wav_path"]
                second_bytes = Path(second_path).read_bytes()

                os.environ["ELEVENLABS_MODEL"] = "eleven_flash_v2_5"
                third = self._run(workflow, output_dir, cache_dir)
                third_path = third["segments"][0]["_wav_path"]

            self.assertEqual(calls, [("eleven_flash_v2_5", "en"), ("eleven_v3", "en")])
            self.assertNotEqual(first_path, second_path)
            self.assertNotEqual(hashlib.sha1(first_bytes).hexdigest(), hashlib.sha1(second_bytes).hexdigest())
            self.assertEqual(first_path, third_path)
            self.assertEqual(first_bytes, Path(third_path).read_bytes())

    def test_language_changes_use_distinct_files_and_round_trip_to_original_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            output_dir = os.path.join(tmp_dir, "output")
            cache_dir = os.path.join(tmp_dir, "cache")
            calls = []

            def synthesize(**kwargs):
                model_id = kwargs["model_id"]
                language_code = kwargs["language_code"]
                calls.append((model_id, language_code))
                return _write_wav(kwargs["wav_path"], _tone_for(model_id, language_code))

            _mock_workflow_engine(workflow, synthesize, os.path.join(tmp_dir, "voice.wav"))

            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3", "ELEVENLABS_TTS_WORKERS": "1"}):
                english_first = self._run(workflow, output_dir, cache_dir, target_language="en")
                english_path = english_first["segments"][0]["_wav_path"]
                english_bytes = Path(english_path).read_bytes()
                vietnamese = self._run(workflow, output_dir, cache_dir, target_language="vi")
                vietnamese_path = vietnamese["segments"][0]["_wav_path"]
                english_again = self._run(workflow, output_dir, cache_dir, target_language="en")

            self.assertEqual(calls, [("eleven_v3", "en"), ("eleven_v3", "vi")])
            self.assertNotEqual(english_path, vietnamese_path)
            self.assertEqual(english_path, english_again["segments"][0]["_wav_path"])
            self.assertEqual(english_bytes, Path(english_again["segments"][0]["_wav_path"]).read_bytes())

    def test_elevenlabs_failure_is_actionable_uncached_and_retry_synthesizes(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            output_dir = os.path.join(tmp_dir, "output")
            cache_dir = os.path.join(tmp_dir, "cache")
            calls = []

            def synthesize(**kwargs):
                calls.append(kwargs)
                if len(calls) == 1:
                    raise RuntimeError("ElevenLabs API Key is invalid (401).")
                return _write_wav(kwargs["wav_path"], 320)

            _mock_workflow_engine(workflow, synthesize, os.path.join(tmp_dir, "voice.wav"))

            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3", "ELEVENLABS_TTS_WORKERS": "1"}):
                with self.assertRaisesRegex(RuntimeError, "Check the API key, model, and target language"):
                    self._run(workflow, output_dir, cache_dir)
                failed_manifest = load_manifest(cache_dir)
                self.assertEqual(failed_manifest["by_cache_key"], {})
                result = self._run(workflow, output_dir, cache_dir)

            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0]["language_code"], "en")
            self.assertEqual(calls[0]["model_id"], "eleven_v3")
            self.assertTrue(Path(result["segments"][0]["_wav_path"]).exists())

    def test_failure_cancels_queued_jobs_and_keeps_prior_successful_cache(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            workflow = VoiceWorkflow(tmp_dir)
            cache_dir = os.path.join(tmp_dir, "cache")
            calls = []

            def synthesize(**kwargs):
                calls.append(kwargs["text"])
                if kwargs["text"] == "fail":
                    raise RuntimeError("API returned 401")
                return _write_wav(kwargs["wav_path"], _tone_for(kwargs["text"]))

            segments = [
                {"text": "keep cache", "start": 0.0, "end": 1.0},
                {"text": "fail", "start": 1.0, "end": 2.0},
                {"text": "queued", "start": 2.0, "end": 3.0},
            ]
            _mock_workflow_engine(workflow, synthesize)
            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3", "ELEVENLABS_TTS_WORKERS": "1"}):
                with self.assertRaises(RuntimeError):
                    workflow._synthesize_segment_wavs(
                        segments=segments,
                        tmp_dir=cache_dir,
                        voice_name="elevenlabs:test-voice",
                        language_code="en",
                    )
                manifest = load_manifest(cache_dir)

            self.assertEqual(calls, ["keep cache", "fail"])
            self.assertEqual(len(manifest["by_cache_key"]), 1)
            saved = next(iter(manifest["by_cache_key"].values()))
            self.assertEqual(saved["text"], "keep cache")
            self.assertTrue(saved["synthesis_succeeded"])
            self.assertTrue(Path(saved["wav_path"]).exists())

    def test_project_signature_only_includes_elevenlabs_model_and_language(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ProjectService(tmp_dir)
            segment = {"text": "same text", "start": 0.0, "end": 1.0}
            base = service.build_voice_signature(
                [segment], voice_name="elevenlabs:test-voice", model_id="eleven_flash_v2_5", language_code="en"
            )
            self.assertNotEqual(
                base,
                service.build_voice_signature(
                    [segment], voice_name="elevenlabs:test-voice", model_id="eleven_v3", language_code="en"
                ),
            )
            self.assertNotEqual(
                base,
                service.build_voice_signature(
                    [segment], voice_name="elevenlabs:test-voice", model_id="eleven_flash_v2_5", language_code="vi"
                ),
            )
            self.assertEqual(
                service.build_voice_signature([segment], voice_name="piper:test-voice"),
                service.build_voice_signature(
                    [segment], voice_name="piper:test-voice", model_id="eleven_v3", language_code="en"
                ),
            )

    def test_remote_runtime_accepts_and_forwards_full_tts_language_contract(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            runtime = EngineRuntime()
            runtime._remote_profile = True
            adapter = RemoteTTSAdapter()
            runtime._instances["tts"] = adapter
            target_path = os.path.join(tmp_dir, "remote.wav")
            audio_bytes = b"mock remote wav"
            with patch("app.engines.remote_tts_adapter.remote_api_post", return_value={"audio_b64": base64.b64encode(audio_bytes).decode("ascii")}) as post:
                runtime.synthesize_segment(
                    text="José",
                    wav_path=target_path,
                    voice="elevenlabs:test-voice",
                    language="en",
                    language_code="en",
                    model_id="eleven_v3",
                )
            self.assertEqual(Path(target_path).read_bytes(), audio_bytes)
            self.assertEqual(post.call_args.args[1]["language"], "en")
            self.assertEqual(post.call_args.args[1]["language_code"], "en")
            self.assertEqual(post.call_args.args[1]["model_id"], "eleven_v3")

    def test_remote_server_synthesis_handler_forwards_optional_model_and_language(self):
        from app.remote_api_server import CapCapRemoteHandler

        with tempfile.TemporaryDirectory() as tmp_dir:
            handler = CapCapRemoteHandler.__new__(CapCapRemoteHandler)

            def fake_synthesize(**kwargs):
                Path(kwargs["wav_path"]).write_bytes(b"mock wav bytes")

            with patch("app.remote_api_server.tempfile.gettempdir", return_value=tmp_dir), \
                 patch("app.remote_api_server.synthesize_text_to_wav_16k_mono", side_effect=fake_synthesize) as synthesize:
                response = handler._handle_tts_synthesize({
                    "text": "José",
                    "voice": "elevenlabs:test-voice",
                    "language_code": "en",
                    "model_id": "eleven_v3",
                })

            self.assertEqual(base64.b64decode(response["audio_b64"]), b"mock wav bytes")
            self.assertEqual(synthesize.call_args.kwargs["language_code"], "en")
            self.assertEqual(synthesize.call_args.kwargs["model_id"], "eleven_v3")
            self.assertNotIn("language", synthesize.call_args.kwargs)

    def test_voice_workers_forward_target_language_and_snapshot_model(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3"}), \
                 patch("runtime_profile.is_remote_profile", return_value=False), \
                 patch("workflows.voice_workflow.VoiceWorkflow") as workflow_cls:
                workflow_cls.return_value.run.return_value = {
                    "voice_track": "voice.wav", "mixed_path": "", "segments": []
                }
                worker = VoiceOverWorker(
                    tmp_dir,
                    [],
                    tmp_dir,
                    "",
                    "fast",
                    "elevenlabs:test-voice",
                    1.0,
                    "off",
                    50,
                    100,
                    target_language="en",
                )
                worker.run()
                voice_kwargs = workflow_cls.return_value.run.call_args.kwargs
                self.assertEqual(voice_kwargs["target_language"], "en")
                self.assertEqual(voice_kwargs["elevenlabs_model_id"], "eleven_v3")
                self.assertEqual(worker.elevenlabs_model_id, "eleven_v3")

            for worker_type, args in (
                (SegmentAudioPreviewWorker, (tmp_dir, 0, "José", "elevenlabs:test-voice", 1.0)),
                (VoiceSamplePreviewWorker, (tmp_dir, "José", "elevenlabs:test-voice", 1.0)),
            ):
                with patch.dict(os.environ, {"ELEVENLABS_MODEL": "eleven_v3"}), \
                     patch("ui.worker_adapters.processing_workers.EngineRuntime") as engine_cls:
                    engine = engine_cls.return_value
                    engine.synthesize_segment.side_effect = lambda **kwargs: _write_wav(kwargs["wav_path"], 270)
                    if worker_type is SegmentAudioPreviewWorker:
                        worker = worker_type(*args, temp_dir=tmp_dir, cache_temp_dir=tmp_dir, target_language="en")
                    else:
                        worker = worker_type(*args, temp_dir=tmp_dir, target_language="en")
                        worker._is_preview_audio_usable = MagicMock(return_value=True)
                        worker._normalize_preview_wav = MagicMock(side_effect=lambda path, temp_dir: path)
                    worker.run()
                    synthesize_kwargs = engine.synthesize_segment.call_args.kwargs
                    self.assertEqual(synthesize_kwargs["language_code"], "en")
                    self.assertEqual(synthesize_kwargs["model_id"], "eleven_v3")

    def test_run_prepare_keeps_video_path_positional_and_omits_unset_model(self):
        runtime = WorkflowRuntime.__new__(WorkflowRuntime)
        runtime.prepare_workflow = MagicMock()
        runtime.run_prepare("source.mp4")
        args, kwargs = runtime.prepare_workflow.run.call_args
        self.assertEqual(args, ("source.mp4",))
        self.assertNotIn("elevenlabs_model_id", kwargs)


if __name__ == "__main__":
    unittest.main()
