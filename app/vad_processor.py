import os
import traceback
import numpy as np
from runtime_paths import bin_path, models_path

_VAD = None
_WINDOW_SIZE = 0
_LOADED_MODEL_PATH = ""


def resolve_vad_model_path() -> str:
    """Resolve the path to silero_vad.onnx from known binary/model locations."""
    candidates = [
        bin_path("silero_vad.onnx"),
        models_path("silero_vad.onnx"),
        models_path("sensevoice", "silero_vad.onnx"),
        os.path.join(bin_path(), "silero_vad.onnx"),
    ]
    for candidate in candidates:
        path = str(candidate or "").strip()
        if path and os.path.isfile(path) and os.path.getsize(path) > 0:
            return os.path.abspath(path)
    return ""


def _ensure_vad(
    model_path: str = "",
    threshold: float = 0.45,
    min_silence_duration: float = 0.45,
    min_speech_duration: float = 0.25,
):
    global _VAD, _WINDOW_SIZE, _LOADED_MODEL_PATH
    resolved_model = model_path or resolve_vad_model_path()

    if _VAD is not None and _VAD is not False and _LOADED_MODEL_PATH == resolved_model:
        return

    if not resolved_model or not os.path.isfile(resolved_model):
        print(f"[VAD] Silero VAD model not found (searched: {resolved_model})")
        _VAD = False
        return

    try:
        import sherpa_onnx

        print(f"[VAD] Loading Silero VAD model from: {resolved_model}")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = resolved_model
        config.silero_vad.threshold = float(threshold)
        config.silero_vad.min_silence_duration = float(min_silence_duration)
        config.silero_vad.min_speech_duration = float(min_speech_duration)
        config.sample_rate = 16000
        config.num_threads = 1

        _WINDOW_SIZE = config.silero_vad.window_size
        _VAD = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        _LOADED_MODEL_PATH = resolved_model
        print(f"[VAD] Initialized OK, window_size={_WINDOW_SIZE}, threshold={threshold}, min_silence={min_silence_duration}s, min_speech={min_speech_duration}s")
    except Exception as exc:
        traceback.print_exc()
        print(f"[VAD] Failed to initialize Silero VAD: {exc}")
        _VAD = False


def post_process_vad_segments(
    raw_segments: list[dict],
    total_duration: float,
    *,
    pre_pad: float = 0.15,
    post_pad: float = 0.20,
    max_merge_gap: float = 0.35,
    max_chunk_duration: float = 12.0,
) -> list[dict]:
    """
    Apply pre/post speech padding and merge adjacent short pauses to create
    natural, cohesive speech chunks for SenseVoice ASR.
    """
    if not raw_segments:
        return []

    # Step 1: Apply pre-speech and post-speech padding
    padded = []
    for seg in raw_segments:
        start = max(0.0, float(seg["start"]) - pre_pad)
        end = min(total_duration, float(seg["end"]) + post_pad)
        if end > start:
            padded.append({"start": start, "end": end})

    if not padded:
        return []

    # Step 2: Merge overlapping or closely adjacent segments within max_chunk_duration
    merged = []
    current = dict(padded[0])

    for nxt in padded[1:]:
        gap = nxt["start"] - current["end"]
        combined_duration = nxt["end"] - current["start"]

        if gap <= max_merge_gap and combined_duration <= max_chunk_duration:
            # Merge adjacent or overlapping segment
            current["end"] = max(current["end"], nxt["end"])
        else:
            merged.append({
                "start": round(current["start"], 3),
                "end": round(current["end"], 3),
            })
            current = dict(nxt)

    merged.append({
        "start": round(current["start"], 3),
        "end": round(current["end"], 3),
    })

    return merged


def get_speech_segments(
    audio: np.ndarray,
    sr: int = 16000,
    *,
    threshold: float = 0.45,
    min_silence_duration: float = 0.45,
    min_speech_duration: float = 0.25,
    pre_pad: float = 0.15,
    post_pad: float = 0.20,
    max_merge_gap: float = 0.35,
    max_chunk_duration: float = 12.0,
) -> list[dict]:
    """
    Detect speech segments in 16kHz mono audio using Silero VAD via sherpa-onnx.
    Returns merged, padded timestamps suitable for SenseVoice transcription.
    """
    global _VAD, _WINDOW_SIZE

    _ensure_vad(
        threshold=threshold,
        min_silence_duration=min_silence_duration,
        min_speech_duration=min_speech_duration,
    )

    if _VAD is False or _VAD is None:
        message = "Silero VAD is unavailable; please ensure silero_vad.onnx is downloaded."
        print(f"[VAD] {message}")
        raise RuntimeError(message)

    if sr != 16000:
        message = f"Silero VAD requires 16000Hz audio, received {sr}Hz."
        print(f"[VAD] {message}")
        raise RuntimeError(message)

    if audio is None or len(audio) == 0:
        return []

    total_duration = float(len(audio)) / float(sr)
    print(f"[VAD] Input audio duration: {total_duration:.2f}s, min={audio.min():.4f}, max={audio.max():.4f}, mean_abs={np.abs(audio).mean():.6f}")

    try:
        if hasattr(_VAD, "reset"):
            _VAD.reset()

        raw_segments = []
        buffer = np.array([], dtype=np.float32)
        offset = 0

        while offset < len(audio):
            remaining = len(audio) - offset
            chunk = audio[offset:offset + remaining]
            buffer = np.concatenate([buffer, chunk])
            offset += remaining

            while len(buffer) >= _WINDOW_SIZE:
                _VAD.accept_waveform(buffer[:_WINDOW_SIZE])
                buffer = buffer[_WINDOW_SIZE:]

            while not _VAD.empty():
                seg = _VAD.front
                start_sample = seg.start
                samples = seg.samples
                end_sample = start_sample + len(samples) if samples is not None and len(samples) > 0 else start_sample

                start = start_sample / sr
                end = end_sample / sr
                if end > start:
                    raw_segments.append({"start": start, "end": end})
                _VAD.pop()

        _VAD.flush()

        while not _VAD.empty():
            seg = _VAD.front
            start_sample = seg.start
            samples = seg.samples
            end_sample = start_sample + len(samples) if samples is not None and len(samples) > 0 else start_sample

            start = start_sample / sr
            end = end_sample / sr
            if end > start:
                raw_segments.append({"start": start, "end": end})
            _VAD.pop()

        print(f"[VAD] Raw speech turns detected: {len(raw_segments)}")

        if not raw_segments:
            return []

        final_segments = post_process_vad_segments(
            raw_segments,
            total_duration,
            pre_pad=pre_pad,
            post_pad=post_pad,
            max_merge_gap=max_merge_gap,
            max_chunk_duration=max_chunk_duration,
        )

        print(f"[VAD] Optimized speech chunks after padding and merging: {len(final_segments)}")
        return final_segments

    except Exception:
        traceback.print_exc()
        message = "Silero VAD execution failed."
        print(f"[VAD] {message}")
        raise RuntimeError(message)

