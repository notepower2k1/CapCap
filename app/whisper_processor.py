import os
import shutil
import threading
import traceback
from pathlib import Path

from runtime_paths import models_path, workspace_root


_WHISPER_MODEL_CACHE: dict[tuple[str, str, str], object] = {}
_WHISPER_MODEL_LOCK = threading.Lock()
_WHISPER_TRANSCRIBE_LOCK = threading.Lock()



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


def _resolve_model_name(model_path):
    if not model_path:
        return "base"

    if os.path.isdir(model_path):
        return model_path

    basename = os.path.basename(model_path)
    if basename in GGML_MODEL_ALIASES:
        return GGML_MODEL_ALIASES[basename]

    stem = Path(model_path).stem
    if stem in KNOWN_FASTER_WHISPER_MODELS:
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


def _faster_whisper_cache_dir():
    cache_dir = Path(models_path("faster_whisper"))
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _detect_faster_whisper_runtime() -> dict:
    _ensure_cuda_runtime_on_path()
    runtime = {
        "device": "cpu",
        "compute_type": "int8",
        "label": "CPU / int8",
    }
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


def _load_whisper_model(model_name):
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

    if not os.path.isdir(str(model_name)):
        model_kwargs["download_root"] = _faster_whisper_cache_dir()
    cache_key = (str(model_name), str(model_kwargs["device"]), str(model_kwargs["compute_type"]))
    with _WHISPER_MODEL_LOCK:
        cached_model = _WHISPER_MODEL_CACHE.get(cache_key)
        if cached_model is not None:
            return cached_model

        try:
            print(f"[Whisper] Loading faster-whisper with {runtime['label']}")
            model = WhisperModel(model_name, **model_kwargs)
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
                model = WhisperModel(model_name, **fallback_kwargs)
                _WHISPER_MODEL_CACHE[fallback_key] = model
                return model
            raise


def load_whisper_model(model_path):
    model_name = _resolve_model_name(model_path)
    return _load_whisper_model(model_name)


def transcribe_audio_with_model(model, audio_path, *, language="auto", task="transcribe"):
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
        try:
            segments, _info = model.transcribe(audio_path, **transcribe_kwargs)
        except Exception as exc:
            error_text = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            if "onnxruntime" not in error_text.lower() and "onnxruntimeerror" not in error_text.lower():
                raise
            print(f"[Whisper] VAD failed with ONNX Runtime, retrying without VAD: {error_text}")
            fallback_kwargs = dict(transcribe_kwargs)
            fallback_kwargs["vad_filter"] = False
            segments, _info = model.transcribe(audio_path, **fallback_kwargs)

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
        for segment in segments
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
