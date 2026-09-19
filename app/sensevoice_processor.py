import os
import threading
import numpy as np
import scipy.io.wavfile as wavfile

_ENABLED = False
_recognizer = None
_current_model_dir = ""
_current_language = ""
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


def load_model(model_dir: str, language: str = "auto"):
    global _recognizer, _current_model_dir, _current_language
    import sherpa_onnx

    lang = _lang_code(language)
    with _lock:
        if _recognizer is not None and _current_model_dir == model_dir and _current_language == lang:
            return
        tokens_path = os.path.join(model_dir, "tokens.txt")
        if not os.path.exists(tokens_path):
            raise FileNotFoundError(f"SenseVoice tokens not found: {tokens_path}")
        onnx_files = [f for f in os.listdir(model_dir) if f.endswith(".onnx")]
        if not onnx_files:
            raise FileNotFoundError(f"No .onnx model found in {model_dir}")
        pref = "model.int8.onnx" if "model.int8.onnx" in onnx_files else onnx_files[0]
        model_path = os.path.join(model_dir, pref)

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


_PUNCT_SPLIT = set("。！？?!;；\n")
_PUNCT_SOFT = set("，,…")


def _is_punct(tok: str) -> bool:
    return any(c in _PUNCT_SPLIT or c in _PUNCT_SOFT or c.isspace() for c in tok)


def _smart_split_sensevoice_tokens(tokens: list, timestamps: list, chunk_start: float, chunk_end: float) -> list[dict]:
    """Split SenseVoice tokens into clean, semantically coherent subtitle segments.

    Uses punctuation marks (terminal vs clause) and actual spoken pauses (inter-token gaps)
    to prevent clumping rapid multi-speaker dialogue together while keeping words intact.
    """
    if not tokens or not timestamps or len(tokens) != len(timestamps):
        return []

    items = [(str(tok), chunk_start + float(t)) for tok, t in zip(tokens, timestamps)]
    subsegs = []
    cur_toks = []
    cur_times = []

    for i, (tok, t) in enumerate(items):
        cur_toks.append(tok)
        cur_times.append(t)

        should_split = False
        if i < len(items) - 1:
            next_word_t = None
            for j in range(i + 1, len(items)):
                if not _is_punct(items[j][0]):
                    next_word_t = items[j][1]
                    break

            last_word_t = t
            if _is_punct(tok):
                for j in range(i - 1, -1, -1):
                    if not _is_punct(items[j][0]):
                        last_word_t = items[j][1]
                        break

            spoken_gap = (next_word_t - last_word_t) if next_word_t is not None else 0.0
            dur = t - cur_times[0]
            words_count = len([c for c in cur_toks if not _is_punct(c)])

            # 1. Dấu kết thúc câu (chấm, hỏi, than): luôn ngắt câu
            if any(c in _PUNCT_SPLIT for c in tok) and words_count >= 1:
                should_split = True
            # 2. Dấu phẩy: ngắt khi vế câu có nghĩa (>= 3 chữ) VÀ (có khoảng nghỉ >= 0.10s hoặc câu đã dài >= 1.5s)
            elif any(c in _PUNCT_SOFT for c in tok) and words_count >= 3:
                if spoken_gap >= 0.10 or dur >= 1.5:
                    should_split = True
            # 3. Macro-pause: chỉ ngắt khi không có dấu câu nếu có khoảng lặng thực sự lớn (>= 0.65s)
            elif spoken_gap >= 0.65 and words_count >= 1:
                should_split = True

        if should_split or i == len(items) - 1:
            txt = "".join(cur_toks).strip()
            is_pure = all(_is_punct(c) for c in txt)
            if is_pure and subsegs:
                subsegs[-1]["text"] += txt
                subsegs[-1]["end"] = round(cur_times[-1] + 0.10, 3)
            elif txt and not is_pure:
                subsegs.append({
                    "start": round(cur_times[0], 3),
                    "end": round(cur_times[-1] + 0.12, 3),
                    "text": txt,
                })
            cur_toks = []
            cur_times = []

    # Hậu xử lý: Gộp các mẩu cảm thán 1 chữ (như "啊，", "嗯。") đứng quá sát câu kế tiếp (< 0.25s)
    refined = []
    for s in subsegs:
        if not refined:
            refined.append(s)
            continue
        prev = refined[-1]
        gap_to_prev = s["start"] - prev["end"]
        chars_prev = len([c for c in prev["text"] if not _is_punct(c)])

        if chars_prev == 1 and gap_to_prev <= 0.25:
            s["text"] = prev["text"] + s["text"]
            s["start"] = prev["start"]
            refined[-1] = s
        else:
            refined.append(s)

    for s in refined:
        s["end"] = min(s["end"], round(chunk_end + 0.20, 3))

    return refined


def _format_time_hms(seconds: float) -> str:
    secs = int(max(0, seconds))
    h = secs // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def transcribe_audio(audio_path: str, model_dir: str, *, language: str = "auto", on_progress=None) -> list[dict]:
    import sherpa_onnx

    load_model(model_dir, language=language)
    sr, audio = wavfile.read(audio_path)
    if audio.dtype != np.float32:
        audio = audio.astype(np.float32) / 32768.0
    if sr != 16000:
        from scipy.signal import resample
        target_len = int(len(audio) * 16000 / sr)
        audio = resample(audio, target_len)

    from vad_processor import get_speech_segments

    total_duration = float(len(audio)) / 16000.0
    segments = get_speech_segments(audio, 16000)
    total_segs = len(segments)
    results = []
    for idx, seg in enumerate(segments):
        start_s = int(seg["start"] * 16000)
        end_s = int(seg["end"] * 16000)
        chunk = audio[start_s:end_s]
        # Do not discard short VAD turns here. A short acknowledgement may
        # be a complete, valid subtitle; the recognizer decides whether it
        # can produce text from the audio.
        if len(chunk) == 0:
            continue

        stream = _recognizer.create_stream()
        stream.accept_waveform(16000, chunk)
        _recognizer.decode_stream(stream)
        res = stream.result
        text = res.text.strip()
        if text:
            subsegs = _smart_split_sensevoice_tokens(res.tokens, res.timestamps, seg["start"], seg["end"])
            if subsegs:
                results.extend(subsegs)
            else:
                results.append({
                    "start": round(seg["start"], 3),
                    "end": round(seg["end"], 3),
                    "text": text,
                })

        pct = min(99, max(1, int((idx + 1) * 100 / total_segs))) if total_segs > 0 else 50
        cur_hms = _format_time_hms(seg["end"])
        tot_hms = _format_time_hms(total_duration) if total_duration > 0 else "--:--"
        log_line = f"[SenseVoice Progress] {idx + 1}/{total_segs} segments ({pct}%) - {cur_hms} / {tot_hms}"
        print(log_line, flush=True)
        if on_progress:
            try:
                on_progress(pct, f"SenseVoice: {pct}% ({idx + 1}/{total_segs})", log_line)
            except Exception:
                pass

    if not results:
        stream = _recognizer.create_stream()
        stream.accept_waveform(16000, audio)
        _recognizer.decode_stream(stream)
        res = stream.result
        text = res.text.strip()
        if text:
            duration = float(len(audio)) / 16000.0
            subsegs = _smart_split_sensevoice_tokens(res.tokens, res.timestamps, 0.0, duration)
            if subsegs:
                results.extend(subsegs)
            else:
                results.append({"start": 0.0, "end": round(duration, 3), "text": text})

    tot_hms = _format_time_hms(total_duration) if total_duration > 0 else "--:--"
    print(f"[SenseVoice Progress] 100% ({tot_hms}) - {len(results)} segments completed", flush=True)
    if on_progress:
        try:
            on_progress(100, f"SenseVoice completed: {len(results)} segments")
        except Exception:
            pass
    return results
