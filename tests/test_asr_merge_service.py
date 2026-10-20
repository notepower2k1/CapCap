import concurrent.futures
import io
import multiprocessing
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from core.models import AudioChunk
from asr_options import normalize_asr_options
from services.asr_merge_service import AsrMergeService, _init_asr_worker


def make_chunks(count, duration):
    return [
        AudioChunk(str(index), f"chunk_{index}.wav", index * duration / count, (index + 1) * duration / count)
        for index in range(count)
    ]


def segments_for(audio_path):
    return [{"start": 0.0, "end": 1.0, "text": audio_path}]


class TestAsrMergeService(unittest.TestCase):
    def test_long_cuda_transcription_finishes_all_chunks_without_cpu_workers(self):
        for count, duration, model_path in ((156, 1781.9, "large-v3"), (2313, 10308.64, "turbo")):
            with self.subTest(model=model_path):
                chunks = make_chunks(count, duration)
                adapter = MagicMock()
                adapter.transcribe_with_model.side_effect = lambda model, path, **kwargs: segments_for(path)
                ready = []
                progress = []
                with patch("whisper_processor._detect_faster_whisper_runtime", return_value={"device": "cuda"}), \
                     patch("services.asr_merge_service.concurrent.futures.ProcessPoolExecutor",
                           side_effect=AssertionError("CUDA must not start a CPU worker")) as pool, \
                     patch.dict(os.environ, {"CAPCAP_HYBRID_ASR": "1", "CAPCAP_HYBRID_ASR_MIN_SECONDS": "60"}), \
                     redirect_stdout(io.StringIO()):
                    results = AsrMergeService().transcribe_chunks(
                        chunks, whisper_adapter=adapter, model_path=model_path, language="zh",
                        ordered_callback=lambda item: ready.append(item["chunk"].chunk_id),
                        on_progress=lambda pct, *args: progress.append(pct),
                    )
                pool.assert_not_called()
                adapter.load_model.assert_called_once_with(model_path)
                self.assertEqual(adapter.transcribe_with_model.call_count, count)
                self.assertEqual([item["segments"] for item in results], [segments_for(c.audio_path) for c in chunks])
                self.assertEqual(ready, [c.chunk_id for c in chunks])
                self.assertEqual(progress[-1], 100)
                self.assertEqual(len(progress), count + 1)
                for call, chunk in zip(adapter.transcribe_with_model.call_args_list, chunks):
                    self.assertEqual(call.args, (adapter.load_model.return_value, chunk.audio_path))
                    self.assertEqual(call.kwargs, {
                        "language": "zh",
                        "use_batched": False,
                        "vad_filter": True,
                        "asr_options": normalize_asr_options(),
                    })

    def test_cuda_resumes_the_missing_first_chunk_and_reuses_completed_cache(self):
        service = AsrMergeService()
        chunks = make_chunks(6, 1800.0)
        adapter = MagicMock()
        adapter.transcribe_with_model.side_effect = lambda model, path, **kwargs: segments_for(path)
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as cache_dir:
            for chunk in chunks[1:]:
                key = service._cache_key(chunk=chunk, model_path="turbo", language="zh", transcription_config={})
                service._save_cached_segments(cache_dir, key, segments_for(chunk.audio_path))
            with patch("whisper_processor._detect_faster_whisper_runtime", return_value={"device": "cuda"}), \
                 patch("services.asr_merge_service.concurrent.futures.ProcessPoolExecutor") as pool, \
                 redirect_stdout(io.StringIO()):
                ready = []
                results = service.transcribe_chunks(
                    chunks, whisper_adapter=adapter, model_path="turbo", language="zh", cache_dir=cache_dir,
                    ordered_callback=lambda item: ready.append(item["chunk"].chunk_id),
                )
                self.assertEqual([item["from_cache"] for item in results], [False] + [True] * 5)
                self.assertEqual(ready, [c.chunk_id for c in chunks])
                adapter.transcribe_with_model.assert_called_once_with(
                    adapter.load_model.return_value, chunks[0].audio_path,
                    language="zh", use_batched=False, vad_filter=True,
                    asr_options=normalize_asr_options(),
                )
                adapter.reset_mock()
                cached_results = service.transcribe_chunks(
                    chunks, whisper_adapter=adapter, model_path="turbo", language="zh", cache_dir=cache_dir,
                )
                self.assertTrue(all(item["from_cache"] for item in cached_results))
                self.assertEqual([item["segments"] for item in cached_results], [segments_for(c.audio_path) for c in chunks])
                adapter.load_model.assert_not_called()
                adapter.transcribe_with_model.assert_not_called()
                pool.assert_not_called()

    def test_cpu_transcription_keeps_the_process_pool(self):
        chunks = make_chunks(4, 1800.0)
        adapter = MagicMock()
        with patch("whisper_processor._detect_faster_whisper_runtime", return_value={"device": "cpu"}), \
             patch("services.asr_merge_service.concurrent.futures.ProcessPoolExecutor") as pool, \
             patch("services.asr_merge_service.os.cpu_count", return_value=4), \
             redirect_stdout(io.StringIO()):
            def submit(job, audio_path, language):
                future = concurrent.futures.Future()
                future.set_result(segments_for(audio_path))
                return future

            pool.return_value.__enter__.return_value.submit.side_effect = submit
            results = AsrMergeService().transcribe_chunks(
                chunks, whisper_adapter=adapter, model_path="small", language="zh",
            )
        pool.assert_called_once_with(
            max_workers=3,
            initializer=_init_asr_worker,
            initargs=("small", normalize_asr_options()),
        )
        self.assertEqual([item["segments"] for item in results], [segments_for(c.audio_path) for c in chunks])
        adapter.transcribe_with_model.assert_not_called()


@unittest.skipUnless(os.name == "nt", "Windows frozen worker startup")
class TestFrozenAsrWorkerStartup(unittest.TestCase):
    def test_spawned_worker_runs_its_task_before_inherited_api_server_mode(self):
        import PyInstaller

        hook = Path(PyInstaller.__file__).parent / "hooks" / "rthooks" / "pyi_rth_multiprocessing.py"
        with tempfile.TemporaryDirectory(dir=ROOT / "temp") as folder:
            bootstrap = Path(folder) / "frozen_startup.py"
            bootstrap.write_text(
                "import runpy, sys\n"
                f"sys.path.insert(0, {str(ROOT / 'app')!r})\n"
                f"runpy.run_path({str(hook)!r})\n"
                f"runpy.run_path({str(ROOT / 'ui' / 'gui.py')!r}, run_name='__main__')\n",
                encoding="utf-8",
            )

            def command_line(**kwargs):
                return [sys.executable, str(bootstrap), "--multiprocessing-fork"] + [
                    f"{key}={value}" for key, value in kwargs.items()
                ]

            env = {
                "CAPCAP_RUN_REMOTE_API_SERVER": "1", "CAPCAP_REMOTE_PRELOAD_MODELS": "0",
                "CAPCAP_REMOTE_API_HOST": "127.0.0.1", "CAPCAP_REMOTE_API_PORT": "0",
            }
            with patch.dict(os.environ, env), patch("multiprocessing.spawn.get_command_line", side_effect=command_line):
                pool = concurrent.futures.ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
                try:
                    pid = pool.submit(os.getpid).result(timeout=10)
                    self.assertIn(pid, pool._processes)
                    self.assertEqual(pool.submit(os.getenv, "CAPCAP_RUN_REMOTE_API_SERVER").result(timeout=5), "1")
                finally:
                    for process in pool._processes.values():
                        if process.is_alive():
                            process.terminate()
                        process.join(timeout=5)
                    pool.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    unittest.main()
