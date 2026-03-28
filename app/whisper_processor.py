import os
from pathlib import Path


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
    return Path(__file__).resolve().parents[1]


def _faster_whisper_cache_dir():
    cache_dir = _workspace_root() / "models" / "faster_whisper"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def _load_whisper_model(model_name):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise ImportError(
            "faster-whisper is not installed. Run `pip install -r requirements.txt` first."
        ) from exc

    model_kwargs = {
        "device": "cpu",
        "compute_type": "int8",
    }

    if not os.path.isdir(str(model_name)):
        model_kwargs["download_root"] = _faster_whisper_cache_dir()

    return WhisperModel(model_name, **model_kwargs)


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
    normalized_language = _normalize_language(language)
    model = _load_whisper_model(model_name)

    segments, _info = model.transcribe(
        audio_path,
        language=normalized_language,
        task=task,
        vad_filter=True,
        beam_size=5,
        word_timestamps=True,
    )

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
