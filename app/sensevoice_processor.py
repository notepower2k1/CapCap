import os
import re
import threading
import numpy as np
import scipy.io.wavfile as wavfile
from runtime_paths import models_path

_ENABLED = False
_recognizer = None
_current_model_dir = ""
_current_language = ""
_current_provider = ""
_lock = threading.Lock()


def is_available() -> bool:
    global _ENABLED
    if _ENABLED:
        return True
    try:
        import sherpa_onnx
        _ENABLED = True
    except ImportError:
        _ENABLED = False
    return _ENABLED


def _lang_code(code: str) -> str:
    if not code or code in ("auto", ""):
        return "auto"
    m = {"vi": "zh", "en": "en", "ja": "ja", "ko": "ko", "zh": "zh", "yue": "yue"}
    return m.get(code.split("-")[0].strip().lower(), "auto")


def resolve_sensevoice_model_dir(model_dir: str = "") -> str:
    """Resolve the directory containing SenseVoice ONNX model and tokens."""
    candidates = [
        model_dir,
        models_path("sensevoice"),
        os.path.join(models_path(), "sensevoice"),
    ]
    for c in candidates:
        candidate_dir = str(c or "").strip()
        if candidate_dir and os.path.isdir(candidate_dir):
            tokens = os.path.join(candidate_dir, "tokens.txt")
            if os.path.isfile(tokens):
                return os.path.abspath(candidate_dir)
    return model_dir or models_path("sensevoice")


def _detect_provider() -> str:
    """Detect if CUDA execution provider is viable for sherpa-onnx."""
    forced_device = str(os.getenv("CAPCAP_DEVICE", "") or "").strip().lower()
    if forced_device == "cpu":
        return "cpu"

    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
        if "CUDAExecutionProvider" in providers:
            return "cuda"
    except Exception:
        pass
    return "cpu"


def load_model(model_dir: str, language: str = "auto", provider: str = "auto"):
    global _recognizer, _current_model_dir, _current_language, _current_provider
    import sherpa_onnx

    model_dir = resolve_sensevoice_model_dir(model_dir)
    lang = _lang_code(language)
    resolved_provider = _detect_provider() if provider == "auto" else provider

    with _lock:
        if (
            _recognizer is not None
            and _current_model_dir == model_dir
            and _current_language == lang
            and _current_provider == resolved_provider
        ):
            return _recognizer

        tokens_path = os.path.join(model_dir, "tokens.txt")
        if not os.path.exists(tokens_path):
            raise FileNotFoundError(f"SenseVoice tokens not found: {tokens_path}")

        onnx_files = [f for f in os.listdir(model_dir) if f.endswith(".onnx")]
        if not onnx_files:
            raise FileNotFoundError(f"No .onnx model found in {model_dir}")

        # Prioritize full-precision model.onnx (FP32/FP16) for maximum accuracy if available,
        # otherwise fallback to quantized model.int8.onnx
        if "model.onnx" in onnx_files:
            pref = "model.onnx"
            model_type_label = "Full Precision (FP32/FP16)"
        elif "model.int8.onnx" in onnx_files:
            pref = "model.int8.onnx"
            model_type_label = "Quantized (INT8)"
        else:
            pref = onnx_files[0]
            model_type_label = pref

        model_path = os.path.join(model_dir, pref)
        print(f"[SenseVoice] Selected model: {pref} [{model_type_label}], tokens: {tokens_path}, lang: {lang}, provider: {resolved_provider}")


        # Try loading with the detected provider first (CUDA/CPU), with graceful fallback to CPU
        for prov in [resolved_provider, "cpu"] if resolved_provider != "cpu" else ["cpu"]:
            try:
                try:
                    _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                        model=model_path,
                        tokens=tokens_path,
                        num_threads=4,
                        use_itn=True,
                        sense_voice_language=lang,
                        provider=prov,
                    )
                except TypeError:
                    # Older sherpa-onnx signature fallback
                    try:
                        _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                            model=model_path,
                            tokens=tokens_path,
                            num_threads=4,
                            use_itn=True,
                            sense_voice_language=lang,
                        )
                    except TypeError:
                        _recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                            model=model_path,
                            tokens=tokens_path,
                            num_threads=4,
                            use_itn=True,
                        )
                _current_model_dir = model_dir
                _current_language = lang
                _current_provider = prov
                print(f"[SenseVoice] Model loaded successfully with provider='{prov}'")
                return _recognizer
            except Exception as exc:
                if prov != "cpu":
                    print(f"[SenseVoice] Provider '{prov}' failed: {exc}. Retrying with CPU...")
                else:
                    raise

    return _recognizer


def _load_audio_16k_mono(audio_path: str) -> np.ndarray:
    """Load audio file as 16000Hz mono float32 numpy array in range [-1.0, 1.0]."""
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio not found at {audio_path}")

    # First attempt: soundfile (supports wav, flac, ogg, etc.)
    try:
        import soundfile as sf
        audio, sr = sf.read(audio_path, dtype="float32")
    except Exception:
        # Fallback to scipy.io.wavfile
        try:
            sr, raw_audio = wavfile.read(audio_path)
            if raw_audio.dtype == np.int16:
                audio = raw_audio.astype(np.float32) / 32768.0
            elif raw_audio.dtype == np.int32:
                audio = raw_audio.astype(np.float32) / 2147483648.0
            elif raw_audio.dtype == np.uint8:
                audio = (raw_audio.astype(np.float32) - 128.0) / 128.0
            else:
                audio = raw_audio.astype(np.float32)
        except Exception as exc:
            raise RuntimeError(f"Failed to read audio file '{audio_path}': {exc}") from exc

    # Convert multi-channel to mono
    if audio.ndim > 1:
        audio = np.mean(audio, axis=1)

    # Ensure float32 contiguous array
    audio = np.ascontiguousarray(audio, dtype=np.float32)

    # Resample to 16000Hz if needed
    if sr != 16000:
        import math
        from scipy.signal import resample_poly
        gcd = math.gcd(16000, sr)
        up = 16000 // gcd
        down = sr // gcd
        audio = resample_poly(audio, up, down).astype(np.float32)

    return audio


def clean_sensevoice_text(text: str) -> str:
    """
    Remove SenseVoice rich metadata tags (<|HAPPY|>, <|Speech|>, <|zh|>, etc.)
    and format spacing cleanly.
    """
    if not text:
        return ""

    # Remove special FunASR/SenseVoice tokens
    cleaned = re.sub(r"<\|[A-Za-z0-9_ -]+\|>", "", text)
    # Remove leading/trailing and redundant whitespace
    cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
    # Remove spaces between Chinese/CJK characters
    cleaned = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s+(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])", "", cleaned)
    return cleaned.strip()


def split_segment_by_punctuation(
    segment: dict,
    *,
    max_chars_per_line: int = 18,
    min_split_duration: float = 2.0,
) -> list[dict]:
    """
    Split a long transcribed segment into natural subtitle lines based on
    punctuation marks (。？！?!；; and ， if too long), interpolating timestamps
    proportionally by text length.
    """
    text = str(segment.get("text", "") or "").strip()
    start = float(segment.get("start", 0.0))
    end = float(segment.get("end", 0.0))
    duration = max(0.1, end - start)

    if not text or duration < min_split_duration or len(text) <= max_chars_per_line:
        return [segment]

    # Pattern for primary sentence breaks
    # (keeps the delimiter attached to the preceding clause)
    parts = []
    # Split on sentence terminators: 。 ？！ ? ! ； ; \n
    raw_sentences = re.split(r"([。？！?!；;\n]+)", text)
    paired = []
    for i in range(0, len(raw_sentences) - 1, 2):
        paired.append(raw_sentences[i] + raw_sentences[i + 1])
    if len(raw_sentences) % 2 == 1 and raw_sentences[-1]:
        paired.append(raw_sentences[-1])

    # If sentences are still excessively long, split on commas (， , 、)
    expanded = []
    for s in paired:
        s = s.strip()
        if not s:
            continue
        if len(s) > max_chars_per_line:
            comma_parts = re.split(r"([，,、]+)", s)
            sub_paired = []
            for j in range(0, len(comma_parts) - 1, 2):
                sub_paired.append(comma_parts[j] + comma_parts[j + 1])
            if len(comma_parts) % 2 == 1 and comma_parts[-1]:
                sub_paired.append(comma_parts[-1])
            expanded.extend([p.strip() for p in sub_paired if p.strip()])
        else:
            expanded.append(s)

    if len(expanded) <= 1:
        return [segment]

    # Calculate proportional timestamps based on character counts
    total_chars = sum(len(p) for p in expanded)
    if total_chars <= 0:
        return [segment]

    results = []
    curr_start = start
    for p in expanded:
        char_ratio = float(len(p)) / float(total_chars)
        p_dur = duration * char_ratio
        p_end = min(end, curr_start + p_dur)
        results.append({
            "start": round(curr_start, 3),
            "end": round(p_end, 3),
            "text": p,
        })
        curr_start = p_end

    # Ensure last segment snaps exactly to end
    if results:
        results[-1]["end"] = round(end, 3)

    return results


def transcribe_audio(
    audio_path: str,
    model_dir: str,
    *,
    language: str = "auto",
    progress_callback=None,
    split_sentences: bool = True,
) -> list[dict]:
    """
    Transcribe audio file using SenseVoice and Silero VAD.
    Returns structured list of segments with start, end, and text.
    """
    import sherpa_onnx
    from vad_processor import get_speech_segments

    load_model(model_dir, language=language)
    audio = _load_audio_16k_mono(audio_path)
    total_audio_duration = float(len(audio)) / 16000.0

    print(f"[SenseVoice] Starting transcription on audio: {audio_path} ({total_audio_duration:.2f}s)")
    if progress_callback:
        progress_callback(5, "Detecting speech with Silero VAD...")

    # Step 1: Voice Activity Detection
    vad_segments = get_speech_segments(audio, 16000)

    results = []
    total_vad = len(vad_segments)

    # Step 2: Transcribe each VAD segment
    if total_vad > 0:
        for idx, seg in enumerate(vad_segments, start=1):
            start_s = int(seg["start"] * 16000)
            end_s = int(seg["end"] * 16000)
            chunk = audio[start_s:end_s]
            if len(chunk) == 0:
                continue

            with _lock:
                stream = _recognizer.create_stream()
                stream.accept_waveform(16000, chunk)
                _recognizer.decode_stream(stream)
                raw_text = stream.result.text.strip()

            text = clean_sensevoice_text(raw_text)
            if text:
                seg_dict = {
                    "start": round(seg["start"], 3),
                    "end": round(seg["end"], 3),
                    "text": text,
                }
                if split_sentences:
                    split_cues = split_segment_by_punctuation(seg_dict)
                    results.extend(split_cues)
                else:
                    results.append(seg_dict)

            if progress_callback:
                percent = 10 + int((idx / total_vad) * 85)
                progress_callback(percent, f"Transcribing Chinese audio ({idx}/{total_vad})...")

    # Step 3: Fallback for audio where VAD detected no segments but audio exists
    if not results:
        print("[SenseVoice] VAD returned no segments; attempting full audio transcription fallback...")
        with _lock:
            stream = _recognizer.create_stream()
            stream.accept_waveform(16000, audio)
            _recognizer.decode_stream(stream)
            raw_text = stream.result.text.strip()

        text = clean_sensevoice_text(raw_text)
        if text:
            seg_dict = {"start": 0.0, "end": round(total_audio_duration, 3), "text": text}
            if split_sentences:
                results.extend(split_segment_by_punctuation(seg_dict))
            else:
                results.append(seg_dict)

    if progress_callback:
        progress_callback(100, "Transcription complete.")

    print(f"[SenseVoice] Completed: generated {len(results)} subtitle cue(s)")
    return results

