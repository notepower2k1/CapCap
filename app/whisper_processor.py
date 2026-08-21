import os
import shutil
import subprocess
import threading
import traceback
from pathlib import Path

from runtime_paths import bin_path, models_path, workspace_root, subprocess_hidden_kwargs, subprocess_text_kwargs
from services.resource_download_service import ResourceDownloadService


_WHISPER_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_WHISPER_BATCHED_PIPELINE_CACHE: dict[int, object] = {}
_WHISPER_MODEL_LOCK = threading.Lock()
_WHISPER_TRANSCRIBE_LOCK = threading.Lock()
_OPENMP_WORKAROUND_APPLIED = False



GGML_MODEL_ALIASES = {
    "ggml-tiny.bin": "tiny",
    "ggml-tiny.en.bin": "tiny.en",
    "ggml-base.bin": "base",
    "ggml-base.en.bin": "base.en",
    "ggml-small.bin": "small",
    "ggml-small.en.bin": "small.en",
    "ggml-medium.bin": "medium",
    "ggml-medium.en.bin": "medium.en",
    "ggml-large-v1.bin": "large-v1",
    "ggml-large-v2.bin": "large-v2",
    "ggml-large-v3.bin": "large-v3",
    "ggml-large.bin": "large-v3",
}

KNOWN_FASTER_WHISPER_MODELS = {
    "tiny",
    "tiny.en",
    "base",
    "base.en",
    "small",
    "small.en",
    "medium",
    "medium.en",
    "large-v1",
    "large-v2",
    "large-v3",
    "distil-large-v2",
    "distil-large-v3",
    "turbo",
}


def _cached_model_snapshot(model_name: str) -> str | None:
    """Return the newest complete Hugging Face cache snapshot for a model."""
    snapshots_dir = Path(models_path(
        "faster_whisper",
        f"models--Systran--faster-whisper-{model_name}",
        "snapshots",
    ))
    if not snapshots_dir.is_dir():
        return None
    snapshots = sorted(
        (path for path in snapshots_dir.iterdir() if path.is_dir() and (path / "model.bin").is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return str(snapshots[0]) if snapshots else None


def _resolve_model_name(model_path):
    if not model_path:
        local_default = models_path("faster_whisper", "base")
        return local_default if os.path.isdir(local_default) else "base"

    model_path = str(model_path).strip()
    if model_path in KNOWN_FASTER_WHISPER_MODELS:
        cached_snapshot = _cached_model_snapshot(model_path)
        if cached_snapshot:
            return cached_snapshot
        local_dir = models_path("faster_whisper", model_path)
        if os.path.isdir(local_dir):
            return local_dir
        return model_path

    if os.path.isdir(model_path):
        return model_path

    basename = os.path.basename(model_path)
    if basename in GGML_MODEL_ALIASES:
        return GGML_MODEL_ALIASES[basename]

    stem = Path(model_path).stem
    if stem in KNOWN_FASTER_WHISPER_MODELS:
        cached_snapshot = _cached_model_snapshot(stem)
        if cached_snapshot:
            return cached_snapshot
        local_dir = models_path("faster_whisper", stem)
        if os.path.isdir(local_dir):
            return local_dir
        return stem

    if model_path in KNOWN_FASTER_WHISPER_MODELS:
        return model_path

    if os.path.exists(model_path):
        raise ValueError(
            f"Model file '{model_path}' looks like a whisper.cpp/ggml model. "
            "Please pass a supported faster-whisper model name or keep using ggml naming like "
            "'ggml-small.bin' so it can be mapped automatically."
        )

    return model_path


def _normalize_language(language):
    if not language or language == "auto":
        return None
    return language


def _workspace_root():
    return Path(workspace_root())


def _candidate_cuda_bin_dirs() -> list[str]:
    candidates = []
    # In a PyInstaller build the canonical bundled pack lives under
    # _internal/bin. Keep it first so Faster-Whisper uses that single copy.
    bundled_cuda_bin = bin_path("cuda12_fw")
    if bundled_cuda_bin:
        candidates.append(str(bundled_cuda_bin))
    workspace_cuda_bin = _workspace_root() / "bin" / "cuda12_fw"
    if workspace_cuda_bin.exists():
        candidates.append(str(workspace_cuda_bin))
    toolkit_root = str(os.getenv("CUDAToolkit_ROOT", "")).strip()
    if toolkit_root:
        candidates.append(os.path.join(toolkit_root, "bin"))
    nvcc_path = shutil.which("nvcc")
    if nvcc_path:
        candidates.append(os.path.dirname(os.path.abspath(nvcc_path)))
    default_root = Path(r"C:\\Program Files\\NVIDIA GPU Computing Toolkit\\CUDA")
    if default_root.exists():
        version_dirs = sorted(default_root.glob("v*"), reverse=True)
        for version_dir in version_dirs:
            candidates.append(str(version_dir / "bin"))
    unique = []
    seen = set()
    for item in candidates:
        normalized = os.path.normcase(os.path.abspath(item)) if item else ""
        if normalized and os.path.isdir(normalized) and normalized not in seen:
            seen.add(normalized)
            unique.append(normalized)
    return unique


def _ensure_cuda_runtime_on_path() -> None:
    current_path = os.environ.get("PATH", "")
    for cuda_bin in _candidate_cuda_bin_dirs():
        if cuda_bin not in current_path:
            os.environ["PATH"] = cuda_bin + os.pathsep + os.environ.get("PATH", "")
        if hasattr(os, "add_dll_directory"):
            try:
                os.add_dll_directory(cuda_bin)
            except Exception:
                pass


def _ensure_openmp_runtime_compat() -> None:
    global _OPENMP_WORKAROUND_APPLIED
    if _OPENMP_WORKAROUND_APPLIED:
        return
    # Windows wheels from Torch / CTranslate2 / Qt-adjacent deps can load different OpenMP runtimes.
    # Allow process startup instead of aborting when both Intel and LLVM runtimes appear.
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")
    _OPENMP_WORKAROUND_APPLIED = True


def _faster_whisper_cache_dir():
    cache_dir = Path(models_path("faster_whisper"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _detect_faster_whisper_runtime() -> dict:
    forced_device = str(os.environ.get("CAPCAP_WHISPER_DEVICE", "") or "").strip().lower()
    cpu_mode = os.environ.get("CAPCAP_DEVICE", "cuda").strip().lower() == "cpu"
    runtime = {
        "device": "cpu",
        "compute_type": "int8",
        "label": "CPU / int8",
    }
    if forced_device == "cpu" or cpu_mode:
        return runtime
    _ensure_openmp_runtime_compat()
    _ensure_cuda_runtime_on_path()
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            runtime = {
                "device": "cuda",
                "compute_type": "int8_float16",
                "label": "CUDA / int8_float16",
            }
    except Exception:
        pass
    return runtime


def _download_whisper_from_custom_repo(model_name: str) -> str | None:
    target_dir = os.path.join(_faster_whisper_cache_dir(), model_name)
    if os.path.isdir(target_dir) and any(
        f for f in os.listdir(target_dir)
        if f.endswith(".bin") and os.path.getsize(os.path.join(target_dir, f)) > 0
    ):
        return target_dir

    repo_id = ResourceDownloadService.HF_RESOURCE_REPO
    base_subfolder = f"faster_whisper/models--Systran--faster-whisper-{model_name}"

    try:
        from huggingface_hub import list_repo_files, hf_hub_download

        repo_files = list_repo_files(repo_id)
        snapshot_prefix = f"{base_subfolder}/snapshots/"

        model_files = [f for f in repo_files if f.startswith(snapshot_prefix) and not f.endswith("/")]

        if not model_files:
            print(f"[Whisper] No model files found in {repo_id}/{snapshot_prefix}")
            return None

        os.makedirs(target_dir, exist_ok=True)

        print(f"[Whisper] Downloading {model_name} ({len(model_files)} files) from {repo_id}...")
        for repo_path in model_files:
            filename = os.path.basename(repo_path)
            dest = os.path.join(target_dir, filename)
            if os.path.exists(dest) and os.path.getsize(dest) > 0:
                continue
            print(f"[Whisper]   {filename}")
            cached_path = hf_hub_download(repo_id=repo_id, filename=repo_path)
            shutil.copy2(cached_path, dest)

        if any(
            f for f in os.listdir(target_dir)
            if f.endswith(".bin") and os.path.getsize(os.path.join(target_dir, f)) > 0
        ):
            print(f"[Whisper] {model_name} ready at {target_dir}")
            return target_dir

    except Exception as e:
        print(f"[Whisper] Custom repo download failed for {model_name}: {e}")
    return None


def _load_whisper_model(model_name):
    _ensure_openmp_runtime_compat()
    _ensure_cuda_runtime_on_path()
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError(
            "faster-whisper is not installed. Run `pip install -r requirements.txt` first."
        ) from exc

    runtime = _detect_faster_whisper_runtime()
    model_kwargs = {
        "device": runtime["device"],
        "compute_type": runtime["compute_type"],
    }
    print(f"[Whisper] Model: {model_name}")

    original_name = str(model_name)
    if not os.path.isdir(str(model_name)):
        model_kwargs["download_root"] = _faster_whisper_cache_dir()
    cache_key = (str(model_name), str(model_kwargs["device"]), str(model_kwargs["compute_type"]))
    with _WHISPER_MODEL_LOCK:
        cached_model = _WHISPER_MODEL_CACHE.get(cache_key)
        if cached_model is not None:
            return cached_model

        try:
            print(f"[Whisper] Loading faster-whisper with {runtime['label']}")
            import threading
            load_error = [None]  # type: ignore
            load_result = [None]  # type: ignore

            def _do_load():
                try:
                    load_result[0] = WhisperModel(model_name, **model_kwargs)
                except Exception as e:
                    load_error[0] = e

            load_thread = threading.Thread(target=_do_load, daemon=True)
            load_thread.start()
            load_thread.join(timeout=120)
            if load_thread.is_alive():
                raise TimeoutError("WhisperModel load timed out after 120s (CUDA may be hanging)")
            if load_error[0]:
                raise load_error[0]
            model = load_result[0]
            # WhisperModel does not publicly expose its selected device. Keep
            # the resolved runtime with the cached instance so batched GPU
            # inference cannot accidentally run after a CPU fallback.
            setattr(model, "_capcap_runtime_device", runtime["device"])
            _WHISPER_MODEL_CACHE[cache_key] = model
            return model
        except Exception as exc:
            if runtime["device"] == "cuda":
                fallback_kwargs = {
                    "device": "cpu",
                    "compute_type": "int8",
                }
                if not os.path.isdir(str(model_name)):
                    fallback_kwargs["download_root"] = _faster_whisper_cache_dir()
                print(f"[Whisper] CUDA load failed, falling back to CPU: {exc}")
                fallback_key = (str(model_name), str(fallback_kwargs["device"]), str(fallback_kwargs["compute_type"]))
                cached_fallback = _WHISPER_MODEL_CACHE.get(fallback_key)
                if cached_fallback is not None:
                    return cached_fallback
                import threading
                fb_error = [None]  # type: ignore
                fb_result = [None]  # type: ignore

                def _do_fb_load():
                    try:
                        fb_result[0] = WhisperModel(model_name, **fallback_kwargs)
                    except Exception as e:
                        fb_error[0] = e

                fb_thread = threading.Thread(target=_do_fb_load, daemon=True)
                fb_thread.start()
                fb_thread.join(timeout=120)
                if fb_thread.is_alive():
                    raise TimeoutError("WhisperModel CPU fallback load timed out after 120s")
                if fb_error[0]:
                    raise fb_error[0]
                model = fb_result[0]
                setattr(model, "_capcap_runtime_device", "cpu")
                _WHISPER_MODEL_CACHE[fallback_key] = model
                return model
            raise


def load_whisper_model(model_path):
    model_name = _resolve_model_name(model_path)
    return _load_whisper_model(model_name)


def unload_whisper_models():
    with _WHISPER_MODEL_LOCK:
        _WHISPER_MODEL_CACHE.clear()
        _WHISPER_BATCHED_PIPELINE_CACHE.clear()
        print("[Whisper] All cached models unloaded")


def _gpu_memory_mb() -> int:
    """Best-effort VRAM query used only to select a conservative batch size."""
    try:
        output = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
            timeout=2,
            **subprocess_text_kwargs(),
        )
        return max(0, int(str(output).splitlines()[0].strip()))
    except Exception:
        return 0


def _whisper_gpu_batch_size() -> int:
    """Return the configured GPU batch size, or a VRAM-safe auto value."""
    if str(os.getenv("CAPCAP_WHISPER_GPU_BATCHED", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return 0
    raw = str(os.getenv("CAPCAP_WHISPER_GPU_BATCH_SIZE", "auto") or "auto").strip().lower()
    if raw in {"0", "off", "false", "no"}:
        return 0
    if raw not in {"", "auto", "default"}:
        try:
            return max(1, min(32, int(raw)))
        except ValueError:
            pass
    # RTX 2060-class cards (about 6 GB) use 8; unknown/low-VRAM cards use 4.
    return 8 if _gpu_memory_mb() >= 5500 else 4


def _is_cuda_memory_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(token in detail for token in (
        "out of memory", "cudaerror_memoryallocation", "cuda memory", "memory allocation", "cudnn_status_alloc_failed",
    ))


def _transcribe_with_vad_fallback(transcribe_fn, audio_path: str, kwargs: dict):
    try:
        return transcribe_fn(audio_path, **kwargs)
    except Exception as exc:
        error_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
        if "onnxruntime" not in error_text.lower() and "onnxruntimeerror" not in error_text.lower():
            raise
        print(f"[Whisper] VAD failed with ONNX Runtime, retrying without VAD: {error_text}")
        fallback_kwargs = dict(kwargs)
        fallback_kwargs["vad_filter"] = False
        return transcribe_fn(audio_path, **fallback_kwargs)


def _get_batched_whisper_pipeline(model):
    key = id(model)
    pipeline = _WHISPER_BATCHED_PIPELINE_CACHE.get(key)
    if pipeline is None:
        from faster_whisper import BatchedInferencePipeline
        pipeline = BatchedInferencePipeline(model=model)
        _WHISPER_BATCHED_PIPELINE_CACHE[key] = pipeline
    return pipeline


def transcribe_audio_with_model(model, audio_path, *, language="auto", task="transcribe", use_batched: bool = True):
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found at {audio_path}")

    normalized_language = _normalize_language(language)
    transcribe_kwargs = {
        "language": normalized_language,
        "task": task,
        "vad_filter": True,
        "beam_size": 5,
        "word_timestamps": True,
    }

    with _WHISPER_TRANSCRIBE_LOCK:
        batch_size = (
            _whisper_gpu_batch_size()
            if use_batched and getattr(model, "_capcap_runtime_device", "") == "cuda"
            else 0
        )
        if batch_size > 1:
            try:
                pipeline = _get_batched_whisper_pipeline(model)
                while batch_size > 1:
                    try:
                        print(f"[Whisper] Batched GPU inference enabled (batch_size={batch_size}).")
                        segments, _info = _transcribe_with_vad_fallback(
                            pipeline.transcribe,
                            audio_path,
                            {**transcribe_kwargs, "batch_size": batch_size},
                        )
                        # Both Faster-Whisper paths return lazy segment
                        # generators. Consume while still inside this retry
                        # block so CUDA OOM is caught and the global GPU lock
                        # protects the actual inference work.
                        raw_segments = list(segments)
                        break
                    except Exception as exc:
                        if not _is_cuda_memory_error(exc):
                            print(f"[Whisper] Batched GPU inference failed; using standard inference: {exc}")
                            raise
                        smaller_batch = batch_size // 2
                        if smaller_batch <= 1:
                            print("[Whisper] Batched GPU inference ran out of VRAM; using standard inference.")
                            raise
                        print(f"[Whisper] Batched GPU inference ran out of VRAM at batch_size={batch_size}; retrying batch_size={smaller_batch}.")
                        batch_size = smaller_batch
                else:
                    raise RuntimeError("No viable batched GPU inference size")
            except Exception:
                # Preserve the existing non-batched path as the reliable
                # fallback for older faster-whisper builds and limited VRAM.
                segments, _info = _transcribe_with_vad_fallback(model.transcribe, audio_path, transcribe_kwargs)
                raw_segments = list(segments)
        else:
            if use_batched and getattr(model, "_capcap_runtime_device", "") == "cuda":
                print("[Whisper] Standard GPU inference (batched inference disabled).")
            segments, _info = _transcribe_with_vad_fallback(model.transcribe, audio_path, transcribe_kwargs)
            raw_segments = list(segments)

    return [
        {
            "start": float(segment.start),
            "end": float(segment.end),
            "text": segment.text.strip(),
            "words": [
                {
                    "start": float(word.start),
                    "end": float(word.end),
                    "text": str(word.word or "").strip(),
                }
                for word in (segment.words or [])
                if getattr(word, "start", None) is not None
                and getattr(word, "end", None) is not None
                and str(getattr(word, "word", "") or "").strip()
            ],
        }
        for segment in raw_segments
        if segment.text and segment.text.strip()
    ]


def transcribe_audio(audio_path, model_path, whisper_path=None, language="auto", task="transcribe"):
    """
    Transcribes audio using faster-whisper while keeping the old compatibility API.

    Args:
        audio_path (str): Path to the input wav file.
        model_path (str): Old ggml file path or faster-whisper model name/local directory.
        whisper_path (str): Unused, kept for compatibility with the old whisper.cpp call sites.
        language (str): Language of the audio ('auto', 'zh', 'en', etc.).
        task (str): 'transcribe' to keep original language, 'translate' to translate to English.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found at {audio_path}")

    model_name = _resolve_model_name(model_path)
    model = load_whisper_model(model_path)
    try:
        return transcribe_audio_with_model(model, audio_path, language=language, task=task)
    except RuntimeError as exc:
        message = str(exc)
        if "cublas64_12.dll" in message or "cannot be loaded" in message:
            print(f"[Whisper] CUDA runtime mismatch detected, falling back to CPU: {message}")
            cpu_kwargs = {
                "device": "cpu",
                "compute_type": "int8",
            }
            if not os.path.isdir(str(model_name)):
                cpu_kwargs["download_root"] = _faster_whisper_cache_dir()
            from faster_whisper import WhisperModel
            cpu_model = WhisperModel(model_name, **cpu_kwargs)
            return transcribe_audio_with_model(cpu_model, audio_path, language=language, task=task)
        raise

if __name__ == "__main__":
    # Test section
    test_audio = os.path.join("temp", "test_audio.wav")
    test_model = os.path.join("models", "ggml-base.bin")
    
    if os.path.exists(test_audio) and os.path.exists(test_model):
        results = transcribe_audio(test_audio, test_model)
        if results:
            for s in results[:5]: # Show first 5 segments
                print(f"[{s['start']} -> {s['end']}] {s['text']}")
    else:
        print("Test files not found.")
