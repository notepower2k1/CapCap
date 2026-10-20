"""Option propagation through local workflows and remote Whisper adapters."""

from __future__ import annotations

import base64
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from asr_options import normalize_asr_options
from core.models import AudioChunk
from engines.remote_whisper_adapter import RemoteWhisperAdapter
from engines.whisper_adapter import WhisperAdapter
from services.engine_runtime import EngineRuntime
from services.project_service import ProjectService
from workflows.prepare_workflow import PrepareWorkflow
from worker_adapters import processing_workers
from worker_adapters.processing_workers import (
    AlternateRangeTranscriptionWorker,
    PrepareWorkflowWorker,
    TranscriptionWorker,
)


class TestAsrFilterWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_local_and_rpc_prepare_workers_send_a_job_snapshot(self):
        requested = {
            "dedup_enabled": False,
            "dedup_similarity": 0.91,
            "hallucination_filter_enabled": False,
            "hallucination_silence_threshold": 4.5,
        }
        expected = normalize_asr_options(requested)
        env = {
            "CAPCAP_ASR_DEDUP_ENABLED": "1",
            "CAPCAP_ASR_DEDUP_SIMILARITY": "0.55",
            "CAPCAP_WHISPER_HALLUCINATION_FILTER": "1",
            "CAPCAP_WHISPER_HALLUCINATION_SILENCE_SECONDS": "1.0",
        }
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, env, clear=False), \
                patch.object(processing_workers, "_get_voice_preview_utils", return_value=SimpleNamespace(voice_provider=lambda _name: "piper")):
            remote = PrepareWorkflowWorker(
                folder, "video.mp4", "subtitle", "fast", "zh", "vi", False,
                False, "", "base", force_remote_api=True, asr_options=requested,
            )
            with patch("remote_api.remote_api_post", return_value={"project_state_path": "project.json"}) as post:
                remote.run()
            self.assertEqual(post.call_args.args[0], "/v1/prepare")
            self.assertEqual(post.call_args.args[1]["asr_options"], expected)

            captured = {}

            class FakePrepareWorkflow:
                def __init__(self, _workspace):
                    pass

                def run(self, video_path, **kwargs):
                    captured.update(kwargs)
                    return SimpleNamespace(project_root=folder)

            local = PrepareWorkflowWorker(
                folder, "video.mp4", "subtitle", "fast", "zh", "vi", False,
                False, "", "base", asr_options=requested,
            )
            with patch("runtime_profile.is_remote_profile", return_value=False), \
                    patch("workflows.prepare_workflow.PrepareWorkflow", FakePrepareWorkflow):
                local.run()
            self.assertEqual(captured["asr_options"], expected)

    def test_direct_and_range_workers_pass_options_only_to_whisper(self):
        requested = {"dedup_enabled": False, "hallucination_silence_threshold": 4.0}
        expected = normalize_asr_options(requested)
        with patch.object(processing_workers, "EngineRuntime") as engine_cls:
            engine = engine_cls.return_value
            direct = TranscriptionWorker("audio.wav", "base", "zh", asr_options=requested)
            direct.run()
            self.assertEqual(engine.transcribe_audio.call_args.args, ("audio.wav", "base"))
            self.assertEqual(engine.transcribe_audio.call_args.kwargs["language"], "zh")
            self.assertEqual(engine.transcribe_audio.call_args.kwargs["asr_options"], expected)

        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            video.touch()
            with patch.object(processing_workers, "EngineRuntime") as engine_cls, \
                    patch.object(processing_workers.subprocess, "run"):
                engine = engine_cls.return_value
                engine.transcribe_audio.return_value = [{"start": 0.0, "end": 1.0, "text": "hello"}]
                range_worker = AlternateRangeTranscriptionWorker(
                    str(video), 3.0, 7.0, "whisper", "base", "en", asr_options=requested,
                )
                range_worker.run()
                self.assertEqual(
                    engine.transcribe_audio.call_args.kwargs["asr_options"], expected
                )
                self.assertTrue(
                    os.path.basename(engine.transcribe_audio.call_args.args[0]).startswith("capcap_range_")
                )

    def test_runtime_and_local_adapter_forward_explicit_options(self):
        options = normalize_asr_options({"hallucination_silence_threshold": 4.25})
        runtime = EngineRuntime()
        adapter = MagicMock()
        runtime._instances["whisper"] = adapter
        runtime.transcribe_audio("audio.wav", "base", language="zh", asr_options=options)
        adapter.transcribe.assert_called_once_with(
            "audio.wav", "base", language="zh", on_progress=None, asr_options=options,
        )

        with patch("engines.whisper_adapter.transcribe_audio", return_value=[] ) as transcribe:
            WhisperAdapter().transcribe("audio.wav", "base", language="zh", asr_options=options)
        transcribe.assert_called_once_with(
            "audio.wav", "base", language="zh", task="transcribe", on_progress=None,
            asr_options=options,
        )

    def test_remote_adapter_and_transcribe_endpoint_keep_the_client_snapshot(self):
        options = normalize_asr_options({"dedup_enabled": False, "hallucination_silence_threshold": 4.0})
        with tempfile.TemporaryDirectory() as folder, \
                patch("engines.remote_whisper_adapter.remote_api_post", return_value={"segments": []}) as post:
            audio = Path(folder) / "test.wav"
            audio.write_bytes(b"fake audio")
            adapter = RemoteWhisperAdapter()
            adapter.transcribe_with_model(
                "base", str(audio), language="zh", use_batched=False, vad_filter=True,
                asr_options=options,
            )
            self.assertEqual(post.call_args.args[0], "/v1/transcribe")
            self.assertEqual(post.call_args.args[1]["asr_options"], options)

        import remote_api_server

        handler = object.__new__(remote_api_server.CapCapRemoteHandler)
        payload = {
            "audio_b64": base64.b64encode(b"fake audio").decode("ascii"),
            "audio_filename": "test.wav",
            "model_name": "base",
            "language": "zh",
            "asr_options": options,
        }
        with patch.object(remote_api_server, "transcribe_audio", return_value=[]) as transcribe:
            handler._handle_transcribe(payload)
        self.assertEqual(transcribe.call_args.kwargs["asr_options"], options)

    def test_workflow_runtime_and_prepare_endpoint_forward_the_snapshot(self):
        options = normalize_asr_options({"dedup_enabled": False, "dedup_similarity": 0.93})
        from services.workflow_runtime import WorkflowRuntime

        runtime = WorkflowRuntime.__new__(WorkflowRuntime)
        runtime.prepare_workflow = MagicMock()
        runtime.prepare_workflow.run.return_value = "prepared"
        self.assertEqual(runtime.run_prepare("video.mp4", asr_options=options), "prepared")
        self.assertEqual(runtime.prepare_workflow.run.call_args.args, ("video.mp4",))
        self.assertEqual(runtime.prepare_workflow.run.call_args.kwargs["asr_options"], options)

        import remote_api_server

        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            video.touch()
            remote_runtime = MagicMock()
            remote_runtime.run_prepare.return_value = SimpleNamespace(project_root=folder)
            remote_runtime.project_state_path.return_value = str(Path(folder) / "project.json")
            handler = object.__new__(remote_api_server.CapCapRemoteHandler)
            with patch.object(remote_api_server, "WorkflowRuntime", return_value=remote_runtime), \
                    patch.object(remote_api_server, "_set_status"), \
                    patch.object(remote_api_server, "_unload_models"):
                handler._handle_prepare({
                    "workspace_root": folder,
                    "video_path": str(video),
                    "asr_options": options,
                })
            self.assertEqual(remote_runtime.run_prepare.call_args.kwargs["asr_options"], options)

    def test_chunk_stream_and_final_merge_use_the_same_snapshot(self):
        options = normalize_asr_options({"dedup_enabled": False, "dedup_similarity": 0.9})
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            video.touch()
            chunk_paths = [Path(folder) / f"chunk_{i}.wav" for i in range(3)]
            for path in chunk_paths:
                path.touch()
            chunks = [
                AudioChunk(str(i), str(path), i * 12.0, (i + 1) * 12.0)
                for i, path in enumerate(chunk_paths)
            ]
            workflow = PrepareWorkflow(folder)
            state = workflow.project_service.ensure_project(str(video))
            workflow.chunking_service.build_chunks = MagicMock(return_value=chunks)

            class FakeAdapter:
                def load_model(self, _model_path):
                    return object()

                def transcribe_with_model(self, _model, audio_path, **_kwargs):
                    chunk_index = int(Path(audio_path).stem.split("_")[1])
                    return [
                        {"start": float(line * 2), "end": float(line * 2 + 1), "text": f"line {line} chunk {chunk_index}"}
                        for line in range(3)
                    ]

            workflow.engine_runtime._instances["whisper"] = FakeAdapter()
            from services.asr_merge_service import AsrMergeService

            original_merge = AsrMergeService.merge_chunk_results
            merge_calls = []
            streamed_merges = []

            def record_merge(service, results, *, asr_options=None):
                merged = original_merge(service, results, asr_options=asr_options)
                merge_calls.append((asr_options, merged))
                return merged

            seen_stream_results = []

            def on_chunk_ready(result):
                seen_stream_results.append(result)
                streamed_merges.append(
                    AsrMergeService().merge_chunk_results(
                        seen_stream_results, asr_options=options
                    )
                )

            with patch("whisper_processor._detect_faster_whisper_runtime", return_value={"device": "cuda"}), \
                    patch.object(AsrMergeService, "merge_chunk_results", record_merge):
                workflow._transcribe_long_audio_chunked(
                    audio_path=str(video), project_state=state, model_path="base", language="zh",
                    asr_options=options,
                    on_chunk_ready=on_chunk_ready,
                )

            self.assertGreaterEqual(len(merge_calls), 1)
            self.assertTrue(all(call_options == options for call_options, _ in merge_calls))
            final_merged = workflow.project_service.load_json_artifact(state, "transcript_merged")
            self.assertEqual(merge_calls[-1][1], final_merged)
            self.assertEqual(streamed_merges[-1], final_merged)

    def test_imported_transcript_survives_changed_filter_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            video = Path(folder) / "video.mp4"
            video.touch()
            workflow = PrepareWorkflow(folder)
            state = workflow.project_service.ensure_project(str(video))
            original = [{"id": 1, "start": 0.5, "end": 1.5, "original_text": "imported cue", "final_text": "imported cue"}]
            workflow.project_service.save_json_artifact(
                state, "transcript_segments", "analysis/transcript_segments.json", original,
            )
            state.set_setting("transcript_imported", True)
            workflow.project_service.save_project(state)

            with patch("workflows.prepare_workflow.ResourceDownloadService.validate_pipeline_runtime", return_value=[]), \
                    patch("workflows.prepare_workflow.is_remote_profile", return_value=False), \
                    patch("workflows.prepare_workflow.bin_path", return_value=str(Path(folder) / "missing_ffmpeg.exe")), \
                    patch.object(workflow.engine_runtime, "extract_audio", return_value=True), \
                    patch.object(workflow.chunking_service, "probe_wav_duration", return_value=4.0), \
                    patch.object(workflow.engine_runtime, "generate_srt") as generate_srt, \
                    patch.object(workflow.engine_runtime, "transcribe_audio") as transcribe:
                original_signature = workflow.project_service.build_transcription_signature
                signature_inputs = {}

                def record_signature(*args, **kwargs):
                    signature_inputs.update(kwargs)
                    return original_signature(*args, **kwargs)

                with patch.object(
                    workflow.project_service,
                    "build_transcription_signature",
                    side_effect=record_signature,
                ):
                    result = workflow.run(
                        str(video), transcription_engine="whisper", skip_translation=True,
                        asr_options={"dedup_enabled": False, "hallucination_silence_threshold": 4.0},
                    )
            self.assertEqual(result.settings["transcript_imported"], True)
            self.assertEqual(transcribe.call_count, 0)
            self.assertIn("asr-filter-v1", signature_inputs["audio_handling_mode"])
            self.assertIn('"dedup_enabled":false', signature_inputs["audio_handling_mode"])
            self.assertIn('"hallucination_silence_threshold":4.0', signature_inputs["audio_handling_mode"])
            saved = workflow.project_service.load_json_artifact(result, "transcript_segments")
            self.assertEqual(saved[0]["original_text"], "imported cue")
            self.assertEqual(generate_srt.call_count, 2)


if __name__ == "__main__":
    unittest.main()
