"""OmniVoice integration contracts; no downloads or inference in automated tests."""
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave
import zipfile

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Initialize the UI's utils namespace before workflows import app.utils.
from ui.main_window import VideoTranslatorGUI
from engines import omnivoice_tts as omni
from services import omnivoice_resources as resources
from services.resource_download_service import ResourceDownloadService
from workflows.voice_workflow import VoiceWorkflow
from app.utils.voice_preview_utils import segment_cache_key, provider_native_speed
from ui.worker_adapters.processing_workers import SegmentAudioPreviewWorker, VoiceSamplePreviewWorker


def write_wav(path, rate=16000):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x20\x10\x10\x20" * 400)
    return str(path)


class OmniVoiceTests(unittest.TestCase):
    def test_progress_reports_completed_cues_and_cache_hits(self):
        with tempfile.TemporaryDirectory() as folder:
            workflow = VoiceWorkflow(folder)
            def generate(**kwargs):
                if kwargs.get("on_progress"):
                    kwargs["on_progress"]("Generating speech with OmniVoice (audio.cpp)...")
                return write_wav(kwargs["wav_path"])
            workflow.engine_runtime.synthesize_segment = MagicMock(side_effect=generate)
            messages = []
            args = dict(
                segments=[dict(start=0, end=1, text="hello"), dict(start=1, end=2, text="world")],
                tmp_dir=folder, voice_name="omnivoice:test-clone", language_code="ja", log=False,
                on_progress=messages.append,
            )
            workflow._synthesize_segment_wavs(**args)
            counts = [message for message in messages if message.startswith("Synthesizing with")]
            self.assertIn("0/2 (0.0%)", counts[0])
            self.assertIn("1/2 (50.0%)", counts[1])
            self.assertIn("2/2 (100.0%)", counts[2])
            messages.clear()
            workflow._synthesize_segment_wavs(**args)
            self.assertTrue(any("2/2 (100.0%)" in message for message in messages))
            self.assertEqual(workflow.engine_runtime.synthesize_segment.call_count, 2)

    def test_quality_control_persists_steps_and_blocks_mid_run_change(self):
        from PySide6.QtWidgets import QApplication, QWidget, QComboBox, QLabel
        app = QApplication.instance() or QApplication([])
        app._capcap_test_mode = True
        host = QWidget()
        host.omnivoice_quality_combo = QComboBox(host)
        host.omnivoice_quality_hint = QLabel(host)
        host.settings = MagicMock()
        host._pipeline_active = False
        for label, steps, description in omni.INFERENCE_PRESETS:
            host.omnivoice_quality_combo.addItem(label, steps)
        with patch.dict(os.environ, {"CAPCAP_OMNIVOICE_STEPS": "32"}):
            host.omnivoice_quality_combo.setCurrentIndex(1)
            VideoTranslatorGUI.on_omnivoice_quality_changed(host)
            self.assertEqual(omni.inference_steps(), 24)
            host.settings.setValue.assert_called_with("omnivoice_steps", 24)
            self.assertTrue(host.omnivoice_quality_hint.text())
            host._pipeline_active = True
            host.omnivoice_quality_combo.setCurrentIndex(3)
            VideoTranslatorGUI.on_omnivoice_quality_changed(host)
            self.assertEqual(host.omnivoice_quality_combo.currentData(), 24)
            self.assertEqual(omni.inference_steps(), 24)
        host.deleteLater()

    def test_preset_steps_change_request_and_cache_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            fixture = Path(write_wav(Path(folder)/"fixture.wav"))
            voice = dict(id="omnivoice:sample", ref_audio=str(fixture), ref_text="Hello")
            runtime = omni.OmniVoiceRuntime()
            runtime.http = MagicMock()
            response = MagicMock()
            response.__enter__.return_value = response
            response.ok = True
            response.iter_content.return_value = [fixture.read_bytes()]
            runtime.http.post.return_value = response
            fingerprints = set()
            def convert(args, **_):
                write_wav(args[-1])
            with patch.object(runtime,"_ensure_server"), patch.object(omni,"list_voices",return_value=[voice]), patch.object(omni.subprocess,"run",side_effect=convert):
                for _, steps, _ in omni.INFERENCE_PRESETS:
                    with patch.dict(os.environ,{"CAPCAP_OMNIVOICE_STEPS":str(steps)}):
                        runtime.synthesize(text="Hello",wav_path=Path(folder)/"output.wav",voice=voice["id"])
                        self.assertEqual(runtime.http.post.call_args.kwargs["json"]["options"]["num_inference_steps"],steps)
                        fingerprints.add(omni.voice_fingerprint(voice["id"]))
                self.assertEqual(len(fingerprints),4)
        for invalid in ("broken", "", 0, 100, 12):
            self.assertEqual(omni.inference_steps(invalid),32)

    def test_vieneu_references_are_shared_without_loading_model_or_copying(self):
        with tempfile.TemporaryDirectory() as folder:
            audio = Path(write_wav(Path(folder)/"reference.wav"))
            source = dict(id="vieneu_clone:story", name="Story (Clone)", ref_audio=str(audio), ref_text="Hello", gender="female")
            import vieneu_tts
            with patch.object(vieneu_tts, "list_vieneu_cloned_voices", return_value=[source, dict(source,id="vieneu_clone:empty",ref_text=""),dict(source,id="vieneu_clone:missing",ref_audio=str(audio)+"missing")]), patch.object(vieneu_tts, "get_cached_vieneu_model", side_effect=AssertionError("must not load VieNeu")), patch.object(omni,"_voices_dir",return_value=Path(folder)/"own"):
                voices = omni.list_voices()
                self.assertEqual(len(voices), 1)
                shared = voices[0]
                self.assertEqual(shared["id"], "omnivoice:vieneu:story")
                self.assertEqual(shared["provider"], "omnivoice")
                self.assertEqual(shared["ref_audio"], str(audio.resolve()))
                self.assertEqual(shared["language"], "multilingual")
                before = omni.voice_fingerprint(shared["id"])
                source["ref_text"] = "Updated transcript"
                self.assertNotEqual(before, omni.voice_fingerprint(shared["id"]))
                self.assertFalse((Path(folder)/"own").exists())

    def test_shared_reference_is_converted_temporarily_and_original_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            original = Path(write_wav(Path(folder)/"reference.wav",rate=16000))
            original_bytes = original.read_bytes()
            voice = dict(id="omnivoice:vieneu:story",ref_audio=str(original),ref_text="Hello",reference_source="vieneu")
            response = MagicMock()
            response.__enter__.return_value = response
            response.ok = True
            response.iter_content.return_value = [original_bytes]
            runtime = omni.OmniVoiceRuntime()
            runtime.http = MagicMock()
            sent_references = []
            def post(url, **kwargs):
                reference = Path(kwargs["json"]["voice_ref"])
                self.assertNotEqual(reference, original)
                self.assertEqual(kwargs["json"]["reference_text"], "Hello")
                with wave.open(str(reference)) as data:
                    self.assertEqual((data.getframerate(),data.getnchannels()),(24000,1))
                sent_references.append(reference)
                return response
            runtime.http.post.side_effect = post
            def convert(args, **kwargs):
                write_wav(args[-1], rate=int(args[args.index("-ar")+1]))
            with patch.object(runtime,"_ensure_server"), patch.object(omni,"list_voices",return_value=[voice]), patch.object(omni.subprocess,"run",side_effect=convert):
                runtime.synthesize(text="Bonjour",wav_path=Path(folder)/"output.wav",voice=voice["id"],language="fr")
            self.assertEqual(original.read_bytes(),original_bytes)
            self.assertTrue(sent_references)
            self.assertFalse(sent_references[0].exists())

    def test_backend_prefers_cuda_and_honors_cpu_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for backend in ("cpu", "cuda", "vulkan"):
                (root/backend).write_bytes(b"MZ")
            with patch.object(omni, "runtime_path", side_effect=lambda backend: root/backend), patch.object(omni, "cuda_library_dir", return_value=root), patch.dict(os.environ, {"CAPCAP_DEVICE": "cuda"}):
                self.assertEqual(omni.selected_backend(), "cuda")
                (root/"cuda").unlink()
                self.assertEqual(omni.selected_backend(), "vulkan")
                (root/"vulkan").unlink()
                self.assertEqual(omni.selected_backend(), "cuda")  # Missing GPU pack must not choose CPU.
                os.environ["CAPCAP_DEVICE"] = "cpu"
                self.assertEqual(omni.selected_backend(), "cpu")

    def test_cuda_install_reuses_complete_shared_pack_or_downloads_dependencies(self):
        for shared in (True, False):
            with self.subTest(shared=shared), tempfile.TemporaryDirectory() as folder:
                service = ResourceDownloadService(folder)
                if shared:
                    pack = Path(folder)/"bin/cuda12_fw"
                    pack.mkdir(parents=True)
                    for name in omni.CUDA_LIBRARIES:
                        (pack/name).write_bytes(b"MZlibrary")
                urls = []
                def archive(url, destination, progress=None, *args):
                    urls.append(url)
                    with zipfile.ZipFile(destination, "w") as package:
                        if "-cudart-" in url:
                            for name in omni.CUDA_LIBRARIES:
                                package.writestr(name, b"MZlibrary")
                        else:
                            package.writestr("audiocpp_server.exe", b"MZserver")
                            package.writestr("ggml-cuda.dll", b"MZggml")
                    if progress:
                        progress(100, "done")
                progress = []
                with patch.object(service, "_download_file", side_effect=archive), patch.object(resources, "unload_omnivoice"):
                    resources.download(service, resources.CUDA_ID, lambda p,m: progress.append(p))
                self.assertEqual(len(urls), 1 if shared else 2)
                self.assertTrue(service.is_resource_installed(resources.CUDA_ID))
                target = Path(folder)/"bin/audio_cpp/cuda"
                self.assertEqual((target/"cublas64_12.dll").exists(), not shared)
                self.assertEqual(progress, sorted(progress))
                with patch.object(omni, "runtime_path", return_value=target/"audiocpp_server.exe"), patch.object(omni, "bin_path", side_effect=lambda *p: str(Path(folder)/"bin"/Path(*p))):
                    self.assertEqual(omni.cuda_library_dir(), pack.resolve() if shared else target.resolve())

    def test_server_reused_and_close_terminates_only_owned_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            binary = root/"audiocpp_server.exe"
            binary.write_bytes(b"MZ")
            model = root/"model.gguf"
            model.write_bytes(b"GGUF")
            runtime = omni.OmniVoiceRuntime()
            runtime.http = MagicMock()
            runtime.http.get.return_value.json.side_effect = lambda: {"data": [{"id": runtime.model_id}]}
            process = MagicMock(pid=123)
            process.poll.return_value = None
            with patch.dict(os.environ, {"CAPCAP_DEVICE": "cpu"}), patch.object(omni, "model_path", return_value=model), patch.object(omni, "runtime_path", return_value=binary), patch.object(omni, "temp_path", return_value=str(root/"sessions")), patch.object(omni, "join_root", side_effect=lambda *p: str(root.joinpath(*p))), patch.object(omni.subprocess, "Popen", return_value=process) as start:
                runtime._ensure_server(None)
                session = Path(runtime._directory.name)
                runtime._ensure_server(None)
                start.assert_called_once()
                runtime.close()
                process.terminate.assert_called_once()
                self.assertFalse(session.exists())
                self.assertTrue(runtime.log_path.exists())

    def test_single_segment_and_sample_workers_forward_language(self):
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        app._capcap_test_mode = True
        with tempfile.TemporaryDirectory() as folder:
            engine = MagicMock()
            engine.synthesize_segment.side_effect = lambda **kw: write_wav(kw["wav_path"])
            engine.trim_tts_boundary_silence.side_effect = lambda **kw: kw["input_wav_path"]
            for worker in (
                SegmentAudioPreviewWorker(folder, 0, "hello", "omnivoice:test-clone", 1., temp_dir=folder, target_language="ja"),
                VoiceSamplePreviewWorker(folder, "hello", "omnivoice:test-clone", 1., temp_dir=folder, target_language="fr"),
            ):
                results = []
                worker.finished.connect(lambda *args: results.append(args))
                with patch("ui.worker_adapters.processing_workers.EngineRuntime", return_value=engine), patch.object(VoiceSamplePreviewWorker, "_normalize_preview_wav", side_effect=lambda p, **kw: p):
                    worker.run()
                self.assertTrue(results)
                self.assertEqual(results[0][-1], "")
                self.assertTrue(Path(results[0][-2]).is_file())
                self.assertEqual(engine.synthesize_segment.call_args.kwargs["language_code"], worker.target_language)

    def test_dispatch_language_and_native_speed(self):
        from tts_processor import synthesize_text_to_wav_16k_mono, validate_voice_language
        with patch.object(omni, "synthesize_omnivoice_wav_16k_mono", return_value="ok") as generate:
            self.assertEqual(synthesize_text_to_wav_16k_mono(text="hello", wav_path="x.wav", voice="omnivoice:test-clone", language_code="ja"), "ok")
        self.assertEqual(generate.call_args.kwargs["language"], "ja")
        validate_voice_language("omnivoice:test-clone", "ja")
        self.assertEqual(provider_native_speed(requested_speed=1.3, provider="omnivoice"), 1.)

    def test_cache_changes_for_reference_and_language(self):
        kwargs = dict(text="hello", voice_name="omnivoice:test-clone", provider_speed=1.)
        with patch.object(omni, "voice_fingerprint", return_value="a"):
            first = segment_cache_key(**kwargs, language_code="vi")
            self.assertNotEqual(first, segment_cache_key(**kwargs, language_code="ja"))
        with patch.object(omni, "voice_fingerprint", return_value="b"):
            self.assertNotEqual(first, segment_cache_key(**kwargs, language_code="vi"))

    def test_project_signature_and_outer_workflow_forward_omnivoice_language(self):
        from services.project_service import ProjectService
        from services.workflow_runtime import WorkflowRuntime
        from ui.worker_adapters.processing_workers import VoiceOverWorker
        with tempfile.TemporaryDirectory() as folder:
            segments = [dict(start=0, end=1, text="hello", voice_name="omnivoice:test-clone")]
            project = ProjectService(folder)
            a = project.build_voice_signature(segments, language_code="ja")
            self.assertNotEqual(a, project.build_voice_signature(segments, language_code="vi"))
            runtime = WorkflowRuntime(folder)
            runtime.voice_workflow = MagicMock()
            runtime.run_voice(segments=segments, output_dir=folder, voice_name="omnivoice:test-clone", target_language="ja")
            self.assertEqual(runtime.voice_workflow.run.call_args.kwargs["target_language"], "ja")
            worker = VoiceOverWorker(folder, segments, folder, "", "fast", "omnivoice:test-clone", 1., "off", 50, 100, target_language="fr")
            workflow = MagicMock()
            with patch("workflows.voice_workflow.VoiceWorkflow", return_value=workflow), patch("runtime_profile.is_remote_profile", return_value=False):
                worker.run()
            self.assertEqual(workflow.run.call_args.kwargs["target_language"], "fr")

    def test_cloned_voice_is_self_contained_and_safe(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(omni, "_voices_dir", return_value=Path(folder)/"voices"), patch.object(omni, "_vieneu_reference_voices", return_value=[]):
            source = write_wav(Path(folder)/"source.wav")
            def convert(args, **_):
                return write_wav(args[-1], rate=24000)
            with patch.object(omni.subprocess, "run", side_effect=convert):
                entry = omni.save_cloned_voice(name="Narrator", audio_path=source, ref_text="Hello")
            Path(source).unlink()
            self.assertTrue(Path(entry["ref_audio"]).is_file())
            self.assertEqual(entry["ref_text"], "Hello")
            root = Path(folder)/"voices"
            (root/"bad.json").write_text('{"name":"bad","audio_file":"../outside.wav","ref_text":"hi"}')
            write_wav(Path(folder)/"outside.wav")
            self.assertEqual(len(omni.list_voices()), 1)

    def test_http_payload_conversion_and_atomic_output(self):
        with tempfile.TemporaryDirectory() as folder:
            raw = Path(write_wav(Path(folder)/"fixture.wav", rate=24000)).read_bytes()
            target = Path(folder)/"result.wav"
            target.write_bytes(b"old")
            runtime = omni.OmniVoiceRuntime()
            response = MagicMock()
            response.__enter__.return_value = response
            response.ok = True
            response.iter_content.return_value = [raw]
            runtime.http = MagicMock()
            runtime.http.post.return_value = response
            voice = dict(id="omnivoice:sample", ref_audio="reference.wav", ref_text="Hello")
            def convert(args, **_):
                self.assertEqual(target.read_bytes(), b"old")
                return write_wav(args[-1])
            with patch.object(runtime, "_ensure_server"), patch.object(omni, "list_voices", return_value=[voice]), patch.object(omni.subprocess, "run", side_effect=convert):
                runtime.synthesize(text="Bonjour", wav_path=target, voice=voice["id"], language="fr")
            payload = runtime.http.post.call_args.kwargs["json"]
            self.assertEqual(payload["voice_ref"], "reference.wav")
            self.assertEqual(payload["reference_text"], "Hello")
            self.assertEqual(payload["options"]["language"], "fr")
            self.assertEqual(payload["options"]["num_inference_steps"], 32)
            with wave.open(str(target)) as audio:
                self.assertEqual((audio.getframerate(), audio.getnchannels()), (16000, 1))

    def test_timeout_stops_owned_server_and_preserves_previous_output(self):
        import requests
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)/"result.wav"
            target.write_bytes(b"old")
            runtime = omni.OmniVoiceRuntime()
            runtime.http = MagicMock()
            runtime.http.post.side_effect = requests.Timeout("busy")
            with patch.object(runtime, "_ensure_server"), patch.object(runtime, "close") as close, patch.object(omni,"list_voices",return_value=[dict(id="omnivoice:test-clone",ref_audio="reference.wav",ref_text="Hello")]):
                with self.assertRaises(requests.Timeout):
                    runtime.synthesize(text="Hello", wav_path=target, voice="omnivoice:test-clone")
            close.assert_called_once()
            self.assertEqual(target.read_bytes(), b"old")

    def test_batch_language_cache_and_failure_without_silent_placeholder(self):
        with tempfile.TemporaryDirectory() as folder:
            workflow = VoiceWorkflow(folder)
            workflow.engine_runtime.synthesize_segment = MagicMock(side_effect=lambda **kw: write_wav(kw["wav_path"]))
            args = dict(segments=[dict(start=0,end=1,text="hello")], tmp_dir=folder, voice_name="omnivoice:test-clone", language_code="ja", log=False)
            first = workflow._synthesize_segment_wavs(**args)
            second = workflow._synthesize_segment_wavs(**args)
            self.assertEqual(first, second)
            workflow.engine_runtime.synthesize_segment.assert_called_once()
            self.assertEqual(workflow.engine_runtime.synthesize_segment.call_args.kwargs["language_code"], "ja")
            args["language_code"] = "fr"
            french = workflow._synthesize_segment_wavs(**args)
            self.assertNotEqual(first, french)
            args["language_code"] = "ja"
            self.assertEqual(first, workflow._synthesize_segment_wavs(**args))
            self.assertEqual(workflow.engine_runtime.synthesize_segment.call_count, 2)
            workflow.engine_runtime.synthesize_segment.side_effect = RuntimeError("GPU out of memory")
            args["language_code"] = "vi"
            with patch.object(workflow, "_write_silence_wav") as silence, self.assertRaisesRegex(RuntimeError, "OmniVoice.*GPU out of memory"):
                workflow._synthesize_segment_wavs(**args)
            silence.assert_not_called()

    def test_resources_download_route_and_zip_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ResourceDownloadService(folder)
            with patch.object(resources, "download") as download:
                service.download_resource(resources.CPU_ID)
                download.assert_called_once_with(service, resources.CPU_ID, None)
            def unsafe(url, destination, *args):
                with zipfile.ZipFile(destination, "w") as package:
                    package.writestr("../escaped.exe", b"MZ")
            with patch.object(service, "_download_file", side_effect=unsafe), patch.object(resources, "unload_omnivoice"), self.assertRaisesRegex(RuntimeError, "Unsafe"):
                resources.download(service, resources.CPU_ID)
            self.assertFalse((Path(folder)/"bin/audio_cpp/cpu/escaped.exe").exists())

    def test_runtime_install_keeps_dlls_and_replaces_complete_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ResourceDownloadService(folder)
            target = Path(folder)/"bin/audio_cpp/cpu"
            target.mkdir(parents=True)
            (target/"old.txt").write_text("old")
            def archive(url, destination, *args):
                with zipfile.ZipFile(destination, "w") as package:
                    package.writestr("audiocpp_server.exe", b"MZtest")
                    package.writestr("runtime.dll", b"dll")
            with patch.object(service, "_download_file", side_effect=archive), patch.object(resources, "unload_omnivoice"):
                resources.download(service, resources.CPU_ID)
            self.assertTrue(service.is_resource_installed(resources.CPU_ID))
            self.assertTrue((target/"runtime.dll").exists())
            self.assertFalse((target/"old.txt").exists())

    def test_resource_model_invalid_download_preserves_installed(self):
        with tempfile.TemporaryDirectory() as folder:
            service = ResourceDownloadService(folder)
            target = Path(folder)/"models/omnivoice"/omni.MODEL_FILE
            target.parent.mkdir(parents=True)
            target.write_bytes(b"old")
            def corrupt(url, destination, *args):
                Path(destination).write_bytes(b"GGUFbad")
            with patch.object(service, "_download_file", side_effect=corrupt), patch.object(resources, "unload_omnivoice"), self.assertRaisesRegex(RuntimeError, "Invalid"):
                resources.download(service, resources.MODEL_ID)
            self.assertEqual(target.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
