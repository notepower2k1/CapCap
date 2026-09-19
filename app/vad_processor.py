import os
import traceback
import numpy as np
from runtime_paths import bin_path

_VAD = None
_WINDOW_SIZE = 0


def _ensure_vad():
    global _VAD, _WINDOW_SIZE
    if _VAD is not None:
        return

    try:
        import sherpa_onnx

        model = os.path.join(bin_path(), "silero_vad.onnx")
        print(f"[VAD] model: {model}, exists={os.path.exists(model)}")

        if not os.path.exists(model):
            print("[VAD] model not found")
            _VAD = False
            return

        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = model
        config.silero_vad.threshold = 0.5
        config.silero_vad.min_silence_duration = 0.28
        config.silero_vad.min_speech_duration = 0.10
        config.sample_rate = 16000
        config.num_threads = 1

        _WINDOW_SIZE = config.silero_vad.window_size

        _VAD = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=30)
        print(f"[VAD] initialized OK, window_size={_WINDOW_SIZE}")

    except Exception:
        traceback.print_exc()
        _VAD = False


def get_speech_segments(audio: np.ndarray, sr: int = 16000) -> list[dict]:
    global _VAD, _WINDOW_SIZE

    _ensure_vad()

    if _VAD is False:
        message = "Silero VAD is unavailable; refusing unsafe full-audio fallback."
        print(f"[VAD] {message}")
        raise RuntimeError(message)

    if sr != 16000:
        message = f"Silero VAD requires 16000Hz audio, received {sr}Hz."
        print(f"[VAD] {message}")
        raise RuntimeError(message)

    if audio is None or len(audio) == 0:
        return []

    print(f"[VAD] audio stats: min={audio.min():.4f} max={audio.max():.4f} mean_abs={np.abs(audio).mean():.6f}")

    try:
        if hasattr(_VAD, "reset"):
            _VAD.reset()

        segments = []
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

                if samples is not None and len(samples) > 0:
                    end_sample = start_sample + len(samples)
                else:
                    end_sample = start_sample

                start = start_sample / sr
                end = end_sample / sr

                if end > start:
                    segments.append({
                        "start": round(start, 3),
                        "end": round(end, 3),
                    })

                _VAD.pop()

        _VAD.flush()

        while not _VAD.empty():
            seg = _VAD.front
            start_sample = seg.start
            samples = seg.samples

            if samples is not None and len(samples) > 0:
                end_sample = start_sample + len(samples)
            else:
                end_sample = start_sample

            start = start_sample / sr
            end = end_sample / sr

            if end > start:
                segments.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                })

            _VAD.pop()

        print(f"[VAD] raw segments: {len(segments)}")

        if not segments:
            # An empty result is a valid "no speech" outcome.  Do not turn
            # it into a full-duration speech segment.
            return []

        return segments

    except Exception:
        traceback.print_exc()
        message = "Silero VAD failed; refusing unsafe full-audio fallback."
        print(f"[VAD] {message}")
        raise RuntimeError(message)
