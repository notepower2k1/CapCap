import os
import subprocess
import uuid
import wave

import numpy as np
import soundfile as sf

from runtime_paths import bin_path, subprocess_hidden_kwargs, subprocess_text_kwargs


def _ffmpeg_path():
    return bin_path("ffmpeg", "ffmpeg.exe")


def _ffprobe_path():
    return bin_path("ffmpeg", "ffprobe.exe")


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs


def _probe_wav_duration_seconds(wav_path: str) -> float:
    if not wav_path or not os.path.exists(wav_path):
        return 0.0
    try:
        info = sf.info(wav_path)
        return max(0.0, float(info.duration))
    except Exception:
        pass
    try:
        with wave.open(wav_path, "rb") as wav_file:
            frame_rate = wav_file.getframerate() or 16000
            frame_count = wav_file.getnframes()
        return max(0.0, float(frame_count) / float(frame_rate))
    except Exception:
        return 0.0


def _atomic_write_wav(output_wav_path: str, data: np.ndarray, sample_rate: int = 16000) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)) or ".", exist_ok=True)
    part_path = f"{output_wav_path}.{uuid.uuid4().hex[:8]}.part.wav"
    try:
        sf.write(part_path, data, sample_rate, format="WAV", subtype="PCM_16")
        info = sf.info(part_path)
        if info.frames < 0:
            raise RuntimeError(f"Generated WAV file is invalid: {part_path}")
        os.replace(part_path, output_wav_path)
        return output_wav_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def change_pcm_speed(
    pcm: np.ndarray,
    sample_rate: int = 16000,
    speed_ratio: float = 1.0,
) -> np.ndarray:
    """Change playback speed of 1D float32 mono PCM audio using PyAV atempo filter graph.
    Preserves pitch and flushes graph completely.
    """
    ratio = max(0.01, float(speed_ratio))
    if abs(ratio - 1.0) < 0.001 or len(pcm) == 0:
        return pcm.copy()

    try:
        import av
        from fractions import Fraction

        # Build chained atempo filter ratios (each must be 0.5 <= r <= 2.0)
        r = ratio
        sub_ratios = []
        while r < 0.5 or r > 2.0:
            if r < 0.5:
                sub_ratios.append(0.5)
                r /= 0.5
            else:
                sub_ratios.append(2.0)
                r /= 2.0
        sub_ratios.append(r)

        graph = av.filter.Graph()
        source = graph.add_abuffer(
            sample_rate=sample_rate,
            format="flt",
            layout="mono",
            time_base=Fraction(1, sample_rate),
        )
        prev_node = source
        for sub_r in sub_ratios:
            tempo_node = graph.add("atempo", f"{sub_r:.6f}")
            prev_node.link_to(tempo_node)
            prev_node = tempo_node
        sink = graph.add("abuffersink")
        prev_node.link_to(sink)
        graph.configure()

        out_parts = []
        chunk_size = 2048
        pts = 0
        mono_pcm = pcm.astype(np.float32)
        for i in range(0, len(mono_pcm), chunk_size):
            chunk = mono_pcm[i : i + chunk_size]
            frame = av.AudioFrame(format="flt", layout="mono", samples=len(chunk))
            frame.sample_rate = sample_rate
            frame.pts = pts
            pts += len(chunk)
            frame.planes[0].update(chunk.tobytes())
            source.push(frame)
            while True:
                try:
                    of = sink.pull()
                    out_parts.append(of.to_ndarray().flatten().astype(np.float32))
                except (av.FFmpegError, EOFError, StopIteration):
                    break

        source.push(None)
        while True:
            try:
                of = sink.pull()
                out_parts.append(of.to_ndarray().flatten().astype(np.float32))
            except (av.FFmpegError, EOFError, StopIteration):
                break

        if out_parts:
            return np.concatenate(out_parts)
        return np.empty(0, dtype=np.float32)
    except Exception:
        pass

    # Fallback to FFmpeg CLI pipe with atempo to preserve pitch (never resample!)
    try:
        ffmpeg = _ffmpeg_path()
        if os.path.exists(ffmpeg):
            filter_chain = _build_atempo_filter(ratio)
            cmd = [
                ffmpeg,
                "-y",
                "-loglevel", "error",
                "-f", "f32le",
                "-ar", str(sample_rate),
                "-ac", "1",
                "-i", "pipe:0",
                "-filter:a", filter_chain,
                "-f", "f32le",
                "pipe:1",
            ]
            proc = subprocess.run(
                cmd,
                input=pcm.astype(np.float32).tobytes(),
                capture_output=True,
                **subprocess_hidden_kwargs(),
            )
            if proc.returncode == 0 and proc.stdout:
                return np.frombuffer(proc.stdout, dtype=np.float32).copy()
    except Exception:
        pass

    raise RuntimeError(f"Failed to change PCM speed by ratio {ratio}: atempo processing failed")


def ffprobe_wav_duration(wav_path: str) -> float:
    """Return the actual duration of a wav file via ffprobe.

    Uses ffprobe's `format=duration` for the most accurate reading —
    important for segment preview/regenerate flows where the wav
    may have been re-encoded and the wave header is stale. Returns
    0.0 if ffprobe is missing or the call fails.
    """
    ffprobe = _ffprobe_path()
    if not os.path.exists(ffprobe):
        return 0.0
    try:
        out = subprocess.run(
            [
                ffprobe, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                wav_path,
            ],
            capture_output=True, timeout=10,
            **subprocess_text_kwargs(),
        )
        if out.returncode != 0:
            return 0.0
        return max(0.0, float(out.stdout.strip()))
    except (ValueError, subprocess.TimeoutExpired, OSError):
        return 0.0


def _build_atempo_filter(speed_ratio: float) -> str:
    ratio = max(0.01, float(speed_ratio))
    filters = []
    while ratio < 0.5 or ratio > 2.0:
        if ratio < 0.5:
            filters.append("atempo=0.5")
            ratio /= 0.5
        else:
            filters.append("atempo=2.0")
            ratio /= 2.0
    filters.append(f"atempo={ratio:.6f}")
    return ",".join(filters)


def fit_wav_to_duration(
    *,
    input_wav_path: str,
    output_wav_path: str,
    target_duration_seconds: float,
    mode: str = "off",
    smart_min_ratio: float = 0.77,
    smart_max_ratio: float = 1.15,
) -> str:
    mode_key = (mode or "off").strip().lower()
    if mode_key == "force fit":
        mode_key = "force"
    if mode_key not in {"smart", "force", "timeline"}:
        return input_wav_path
    if not os.path.exists(input_wav_path):
        raise FileNotFoundError(f"Input wav not found: {input_wav_path}")

    source_duration = _probe_wav_duration_seconds(input_wav_path)
    target_duration = max(0.0, float(target_duration_seconds))
    if source_duration <= 0.0 or target_duration <= 0.0:
        return input_wav_path

    fit_ratio = target_duration / source_duration

    # In-process native execution via SoundFile + change_pcm_speed
    try:
        data, sr = sf.read(input_wav_path, dtype="float32")
        mono = np.mean(data, axis=1) if data.ndim == 2 else data

        if mode_key == "timeline":
            if abs(fit_ratio - 1.0) < 0.02:
                return input_wav_path
            target_samples = max(1, int(round(target_duration * sr)))
            out_pcm = mono[:target_samples]
            return _atomic_write_wav(output_wav_path, out_pcm, sample_rate=sr)

        elif mode_key == "smart":
            if abs(fit_ratio - 1.0) < 0.02:
                return input_wav_path
            if fit_ratio < 1.0:
                if fit_ratio < smart_min_ratio:
                    return input_wav_path
                atempo_ratio = 1.0 / fit_ratio
                out_pcm = change_pcm_speed(mono, sample_rate=sr, speed_ratio=atempo_ratio)
                return _atomic_write_wav(output_wav_path, out_pcm, sample_rate=sr)
            else:
                target_samples = max(1, int(round(target_duration * sr)))
                out_pcm = mono[:target_samples]
                return _atomic_write_wav(output_wav_path, out_pcm, sample_rate=sr)

        else:
            # force mode
            if abs(fit_ratio - 1.0) < 0.02:
                return input_wav_path
            if fit_ratio > 1.0 and fit_ratio > smart_max_ratio:
                return input_wav_path
            atempo_ratio = 1.0 / fit_ratio
            out_pcm = change_pcm_speed(mono, sample_rate=sr, speed_ratio=atempo_ratio)
            return _atomic_write_wav(output_wav_path, out_pcm, sample_rate=sr)
    except Exception:
        pass

    # Fallback to FFmpeg CLI
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    part_path = f"{output_wav_path}.{uuid.uuid4().hex[:8]}.part.wav"

    if mode_key == "timeline":
        if abs(fit_ratio - 1.0) < 0.02:
            return input_wav_path
        cmd = [
            ffmpeg, "-y", "-i", input_wav_path,
            "-t", str(target_duration),
            "-ar", "16000", "-ac", "1",
            part_path,
        ]
    elif mode_key == "smart":
        if abs(fit_ratio - 1.0) < 0.02:
            return input_wav_path
        if fit_ratio < 1.0:
            if fit_ratio < smart_min_ratio:
                return input_wav_path
            atempo_ratio = 1.0 / fit_ratio
            filter_chain = _build_atempo_filter(atempo_ratio)
            cmd = [
                ffmpeg, "-y", "-i", input_wav_path,
                "-filter:a", filter_chain,
                "-ar", "16000", "-ac", "1",
                part_path,
            ]
        else:
            cmd = [
                ffmpeg, "-y", "-i", input_wav_path,
                "-t", str(target_duration),
                "-ar", "16000", "-ac", "1",
                part_path,
            ]
    else:
        if abs(fit_ratio - 1.0) < 0.02:
            return input_wav_path
        if fit_ratio > 1.0 and fit_ratio > smart_max_ratio:
            return input_wav_path
        filter_chain = _build_atempo_filter(1.0 / fit_ratio)
        cmd = [
            ffmpeg, "-y", "-i", input_wav_path,
            "-filter:a", filter_chain,
            "-ar", "16000", "-ac", "1",
            part_path,
        ]

    try:
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg fit failed:\n{proc.stderr or proc.stdout}")
        os.replace(part_path, output_wav_path)
        return output_wav_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def change_wav_speed(
    *,
    input_wav_path: str,
    output_wav_path: str,
    speed_ratio: float,
) -> str:
    if not os.path.exists(input_wav_path):
        raise FileNotFoundError(f"Input wav not found: {input_wav_path}")

    ratio = max(0.01, float(speed_ratio))
    if abs(ratio - 1.0) < 0.02:
        return input_wav_path

    # Try in-process PyAV atempo first
    try:
        data, sr = sf.read(input_wav_path, dtype="float32")
        mono = np.mean(data, axis=1) if data.ndim == 2 else data
        speed_data = change_pcm_speed(mono, sample_rate=sr, speed_ratio=ratio)
        return _atomic_write_wav(output_wav_path, speed_data, sample_rate=sr)
    except Exception:
        pass

    # Fallback to FFmpeg CLI if PyAV fails
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    filter_chain = _build_atempo_filter(ratio)
    part_path = f"{output_wav_path}.{uuid.uuid4().hex[:8]}.part.wav"
    cmd = [
        ffmpeg,
        "-y",
        "-i", input_wav_path,
        "-filter:a", filter_chain,
        "-ar", "16000",
        "-ac", "1",
        part_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg speed adjustment failed:\n{proc.stderr or proc.stdout}")
        os.replace(part_path, output_wav_path)
        return output_wav_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def trim_trailing_silence(
    *,
    input_wav_path: str,
    output_wav_path: str,
    silence_threshold: float = -40.0,
    min_silence_duration: float = 0.5,
) -> str:
    """Remove trailing silence from a wav file using in-memory PCM energy detection.
    Keeps audio up to the last detected sound, plus 100ms padding.
    Returns output_wav_path if trimming was applied, or input_wav_path if no trailing silence.
    """
    if not os.path.exists(input_wav_path):
        return input_wav_path

    # Try in-process SoundFile / NumPy first
    try:
        data, sr = sf.read(input_wav_path, dtype="float32")
        mono = np.mean(data, axis=1) if data.ndim == 2 else data

        threshold_amp = 10.0 ** (float(silence_threshold) / 20.0)
        block_size = int(sr * 0.01)  # 10ms block
        if block_size > 0 and len(mono) >= block_size:
            num_blocks = len(mono) // block_size
            blocks = mono[: num_blocks * block_size].reshape(num_blocks, block_size)
            rms = np.sqrt(np.mean(blocks ** 2, axis=1))
            active_indices = np.where(rms > threshold_amp)[0]

            if len(active_indices) > 0:
                last_active_block = active_indices[-1]
                last_sound_sample = min(len(mono), (last_active_block + 1) * block_size)
                trailing_silence_duration = (len(mono) - last_sound_sample) / float(sr)

                if trailing_silence_duration >= min_silence_duration:
                    padding_samples = int(0.1 * sr)
                    trim_end = min(len(mono), last_sound_sample + padding_samples)
                    trimmed_pcm = data[:trim_end] if data.ndim == 2 else mono[:trim_end]
                    return _atomic_write_wav(output_wav_path, trimmed_pcm, sample_rate=sr)
            return input_wav_path
    except Exception:
        pass

    # Fallback to FFmpeg CLI silencedetect
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        return input_wav_path
    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    detect_cmd = [
        ffmpeg, "-y", "-i", input_wav_path,
        "-af", f"silencedetect=noise={silence_threshold}dB:d={min_silence_duration}",
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(
            detect_cmd, capture_output=True, timeout=60,
            **subprocess_text_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return input_wav_path
    if proc.returncode != 0:
        return input_wav_path

    last_end = 0.0
    for line in proc.stderr.splitlines():
        if "silence_end" in line:
            try:
                parts = line.split()
                for i, p in enumerate(parts):
                    if p == "silence_end":
                        last_end = float(parts[i + 1])
                        break
            except (ValueError, IndexError):
                continue
    if last_end <= 0.0:
        return input_wav_path

    padding = 0.1
    trim_to = last_end + padding
    part_path = f"{output_wav_path}.{uuid.uuid4().hex[:8]}.part.wav"
    cmd = [
        ffmpeg, "-y", "-i", input_wav_path,
        "-t", str(trim_to),
        "-ar", "16000", "-ac", "1",
        part_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            return input_wav_path
        os.replace(part_path, output_wav_path)
        return output_wav_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


def _require_pydub():
    try:
        ffmpeg = _ffmpeg_path()
        ffprobe = _ffprobe_path()
        ffmpeg_dir = os.path.dirname(ffmpeg)
        if ffmpeg_dir and os.path.isdir(ffmpeg_dir):
            current_path = os.environ.get("PATH", "")
            path_entries = current_path.split(os.pathsep) if current_path else []
            normalized_dir = os.path.normcase(os.path.normpath(ffmpeg_dir))
            normalized_entries = {
                os.path.normcase(os.path.normpath(entry))
                for entry in path_entries
                if entry
            }
            if normalized_dir not in normalized_entries:
                os.environ["PATH"] = ffmpeg_dir + os.pathsep + current_path if current_path else ffmpeg_dir

        from pydub import AudioSegment
        # Point pydub to our bundled ffmpeg to avoid PATH warnings on Windows.
        if os.path.exists(ffmpeg):
            AudioSegment.converter = ffmpeg
            AudioSegment.ffmpeg = ffmpeg
        if os.path.exists(ffprobe):
            AudioSegment.ffprobe = ffprobe
    except Exception as e:
        raise ImportError(
            "Missing dependency 'pydub'.\n"
            "Please run:\n"
            "python -m pip install pydub\n"
            f"Original error: {e}"
        ) from e


def _merge_ducking_ranges(
    *,
    segments: list,
    audio_length_ms: int,
    attack_ms: int,
    release_ms: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for seg in segments or []:
        try:
            start_ms = int(max(0.0, float(seg.get("start", 0.0))) * 1000.0)
            end_ms = int(max(0.0, float(seg.get("end", 0.0))) * 1000.0)
        except (TypeError, ValueError, AttributeError):
            continue
        if end_ms <= start_ms:
            continue
        duck_start = max(0, start_ms - max(0, attack_ms))
        duck_end = min(audio_length_ms, end_ms + max(0, release_ms))
        if duck_end <= duck_start:
            continue
        if not ranges or duck_start > ranges[-1][1]:
            ranges.append((duck_start, duck_end))
        else:
            prev_start, prev_end = ranges[-1]
            ranges[-1] = (prev_start, max(prev_end, duck_end))
    return ranges


def _apply_timeline_ducking(
    *,
    background_audio,
    ducking_ranges: list[tuple[int, int]],
    duck_amount_db: float,
    attack_ms: int,
    release_ms: int,
):
    if not ducking_ranges:
        return background_audio

    processed = background_audio
    for duck_start, duck_end in ducking_ranges:
        clip = processed[duck_start:duck_end]
        if len(clip) <= 0:
            continue

        attenuated = clip + float(duck_amount_db)
        fade_in_ms = min(max(0, attack_ms), len(attenuated))
        fade_out_ms = min(max(0, release_ms), len(attenuated))
        if fade_in_ms > 0:
            attenuated = attenuated.fade(from_gain=0.0, to_gain=float(duck_amount_db), start=0, duration=fade_in_ms)
        if fade_out_ms > 0:
            fade_out_start = max(0, len(attenuated) - fade_out_ms)
            attenuated = attenuated.fade(
                from_gain=float(duck_amount_db),
                to_gain=0.0,
                start=fade_out_start,
                duration=fade_out_ms,
            )
        processed = processed[:duck_start] + attenuated + processed[duck_end:]
    return processed


def _resample_audio(data: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1D float32 audio data to target_sr using best available method."""
    if orig_sr == target_sr:
        return data
    try:
        import soxr
        return soxr.resample(data, orig_sr, target_sr)
    except Exception:
        try:
            from scipy import signal
            gcd = int(np.gcd(orig_sr, target_sr))
            up = target_sr // gcd
            down = orig_sr // gcd
            return signal.resample_poly(data, up, down).astype(np.float32)
        except Exception:
            num_samples = max(1, int(round(len(data) * target_sr / orig_sr)))
            x_old = np.linspace(0, 1, len(data), endpoint=False)
            x_new = np.linspace(0, 1, num_samples, endpoint=False)
            return np.interp(x_new, x_old, data).astype(np.float32)


def _read_audio_to_mono_float32(wav_path: str, target_sr: int = 16000) -> tuple[np.ndarray, int]:
    """Read any audio file to 1D float32 numpy array at target_sr (16000Hz)."""
    try:
        data, sr = sf.read(wav_path, dtype="float32", always_2d=False)
        if data.ndim > 1:
            data = np.mean(data, axis=-1)
        if sr != target_sr:
            data = _resample_audio(data, sr, target_sr)
        return data.astype(np.float32), target_sr
    except Exception:
        _require_pydub()
        from pydub import AudioSegment
        clip = AudioSegment.from_file(wav_path).set_frame_rate(target_sr).set_channels(1)
        samples = np.array(clip.get_array_of_samples(), dtype=np.float32)
        if clip.sample_width == 2:
            samples /= 32768.0
        elif clip.sample_width == 4:
            samples /= 2147483648.0
        elif clip.sample_width == 1:
            samples = (samples - 128.0) / 128.0
        return samples.astype(np.float32), target_sr


def build_voice_track_from_srt_segments(
    *,
    segments: list,
    tts_wav_paths: list,
    output_wav_path: str,
    total_duration_ms: int | None = None,
    gain_db: float = 0.0,
) -> str:
    """
    Build a single voice track by placing each segment wav at its start time.

    Uses an in-place float32 numpy buffer to achieve O(N) performance and avoid
    large-scale memory reallocations on multi-hour media.
    segments: list of dicts {start: seconds, end: seconds, text: str}
    tts_wav_paths: list of wav paths aligned to segments index
    """
    if len(segments) != len(tts_wav_paths):
        raise ValueError("segments and tts_wav_paths length mismatch")

    target_sr = 16000
    linear_gain = 10.0 ** (gain_db / 20.0) if gain_db else 1.0

    if total_duration_ms is not None:
        init_duration_ms = max(0, int(total_duration_ms))
    else:
        max_end = 0.0
        for seg in segments:
            max_end = max(max_end, float(seg.get("end", 0.0)))
        init_duration_ms = int(max_end * 1000) + 500

    total_samples = int(init_duration_ms * target_sr / 1000)
    base_buffer = np.zeros(max(0, total_samples), dtype=np.float32)

    for idx, (seg, wav_path) in enumerate(zip(segments, tts_wav_paths)):
        if not wav_path or not os.path.exists(wav_path):
            continue
        start_ms = int(float(seg.get("start", 0.0)) * 1000)
        end_ms = int(float(seg.get("end", 0.0)) * 1000)
        max_len = max(0, end_ms - start_ms)

        clip_data, _ = _read_audio_to_mono_float32(wav_path, target_sr=target_sr)
        if clip_data.size == 0:
            continue

        if linear_gain != 1.0:
            clip_data = clip_data * linear_gain

        clip_len_ms = int(round(len(clip_data) * 1000.0 / target_sr))

        if max_len > 0 and clip_len_ms < max_len:
            gap_ms = max_len - clip_len_ms
            clip_end_ms = start_ms + clip_len_ms
            if idx + 1 < len(segments):
                next_start_ms = int(float(segments[idx + 1].get("start", 0.0)) * 1000)
                next_gap = next_start_ms - clip_end_ms
                if 0 < next_gap <= 20:
                    overlap_ms = 10
                    extend_ms = min(next_gap + overlap_ms, clip_len_ms)
                    fade_samples = int(extend_ms * target_sr / 1000)
                    if 0 < fade_samples <= len(clip_data):
                        fade_curve = np.linspace(1.0, 0.0, fade_samples, endpoint=True, dtype=np.float32)
                        clip_data[-fade_samples:] *= fade_curve
                    gap_ms = 0
            if gap_ms > 0:
                fade_ms = min(gap_ms, 50)
                fade_samples = int(fade_ms * target_sr / 1000)
                if 0 < fade_samples <= len(clip_data):
                    fade_curve = np.linspace(1.0, 0.0, fade_samples, endpoint=True, dtype=np.float32)
                    clip_data[-fade_samples:] *= fade_curve

        start_sample = max(0, int(start_ms * target_sr / 1000))
        end_sample = start_sample + len(clip_data)

        if end_sample > base_buffer.size:
            pad_size = max(end_sample - base_buffer.size, target_sr)
            base_buffer = np.pad(base_buffer, (0, pad_size))

        base_buffer[start_sample:end_sample] += clip_data

    peak = float(np.max(np.abs(base_buffer))) if base_buffer.size else 0.0
    if peak > 1.0:
        base_buffer = (base_buffer / peak) * 0.999

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    sf.write(output_wav_path, base_buffer, target_sr, subtype="PCM_16")
    return output_wav_path


def mix_voice_with_background(
    *,
    background_wav_path: str,
    voice_wav_path: str,
    output_wav_path: str,
    background_gain_db: float = 0.0,
    voice_gain_db: float = 0.0,
    ducking_mode: str = "off",
    ducking_segments: list | None = None,
    ducking_amount_db: float = 0.0,
    ducking_threshold: float = 0.015,
    ducking_ratio: float = 10.0,
    ducking_attack_ms: float = 15.0,
    ducking_release_ms: float = 350.0,
) -> str:
    if not os.path.exists(background_wav_path):
        raise FileNotFoundError(f"Background file not found: {background_wav_path}")
    if not os.path.exists(voice_wav_path):
        raise FileNotFoundError(f"Voice file not found: {voice_wav_path}")

    mode_key = str(ducking_mode or "off").strip().lower()
    if mode_key in {"timeline", "segments", "subtitle"}:
        _require_pydub()
        from pydub import AudioSegment

        bg = AudioSegment.from_file(background_wav_path).set_frame_rate(16000).set_channels(1)
        vc = AudioSegment.from_file(voice_wav_path).set_frame_rate(16000).set_channels(1)

        if background_gain_db:
            bg = bg + background_gain_db
        if voice_gain_db:
            vc = vc + voice_gain_db

        if len(vc) > len(bg):
            bg = bg + AudioSegment.silent(duration=(len(vc) - len(bg)), frame_rate=16000)
        elif len(bg) > len(vc):
            vc = vc + AudioSegment.silent(duration=(len(bg) - len(vc)), frame_rate=16000)

        ducking_ranges = _merge_ducking_ranges(
            segments=list(ducking_segments or []),
            audio_length_ms=len(bg),
            attack_ms=int(max(0.0, float(ducking_attack_ms))),
            release_ms=int(max(0.0, float(ducking_release_ms))),
        )
        ducked_bg = _apply_timeline_ducking(
            background_audio=bg,
            ducking_ranges=ducking_ranges,
            duck_amount_db=float(ducking_amount_db),
            attack_ms=int(max(0.0, float(ducking_attack_ms))),
            release_ms=int(max(0.0, float(ducking_release_ms))),
        )

        mixed = ducked_bg.overlay(vc)
        os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
        mixed.export(output_wav_path, format="wav")
        return output_wav_path

    if mode_key in {"auto", "duck", "ducking", "sidechain"}:
        ffmpeg = _ffmpeg_path()
        if not os.path.exists(ffmpeg):
            raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

        os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
        bg_volume = f"volume={float(background_gain_db):+.2f}dB"
        voice_volume = f"volume={float(voice_gain_db):+.2f}dB"
        filter_complex = (
            f"[0:a]{bg_volume}[bg];"
            f"[1:a]{voice_volume},asplit=2[vc_sc][vc_mix];"
            f"[bg][vc_sc]sidechaincompress="
            f"threshold={max(0.0001, float(ducking_threshold)):.4f}:"
            f"ratio={max(1.0, float(ducking_ratio)):.2f}:"
            f"attack={max(0.0, float(ducking_attack_ms)):.1f}:"
            f"release={max(0.0, float(ducking_release_ms)):.1f}:"
            f"makeup=1[ducked];"
            "[ducked][vc_mix]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]"
        )
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            background_wav_path,
            "-i",
            voice_wav_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[mixed]",
            "-ar",
            "16000",
            "-ac",
            "1",
            output_wav_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg ducking mix failed:\n{proc.stderr or proc.stdout}")
        return output_wav_path

    _require_pydub()
    from pydub import AudioSegment

    bg = AudioSegment.from_file(background_wav_path).set_frame_rate(16000).set_channels(1)
    vc = AudioSegment.from_file(voice_wav_path).set_frame_rate(16000).set_channels(1)

    if background_gain_db:
        bg = bg + background_gain_db
    if voice_gain_db:
        vc = vc + voice_gain_db

    # Ensure output covers the longer one
    if len(vc) > len(bg):
        bg = bg + AudioSegment.silent(duration=(len(vc) - len(bg)), frame_rate=16000)
    elif len(bg) > len(vc):
        vc = vc + AudioSegment.silent(duration=(len(bg) - len(vc)), frame_rate=16000)

    mixed = bg.overlay(vc)
    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    mixed.export(output_wav_path, format="wav")
    return output_wav_path


def mix_original_with_dub(
    *,
    original_wav_path: str,
    dub_wav_path: str,
    output_wav_path: str,
    original_gain_db: float = 0.0,
    dub_gain_db: float = 0.0,
) -> str:
    """Mix original audio (A1) with dub audio (A2) at specified gain levels."""
    if not os.path.exists(original_wav_path):
        raise FileNotFoundError(f"Original file not found: {original_wav_path}")
    if not os.path.exists(dub_wav_path):
        raise FileNotFoundError(f"Dub file not found: {dub_wav_path}")

    _require_pydub()
    from pydub import AudioSegment

    original = AudioSegment.from_file(original_wav_path).set_frame_rate(16000).set_channels(1)
    dub = AudioSegment.from_file(dub_wav_path).set_frame_rate(16000).set_channels(1)

    if original_gain_db:
        original = original + original_gain_db
    if dub_gain_db:
        dub = dub + dub_gain_db

    if len(dub) > len(original):
        original = original + AudioSegment.silent(duration=(len(dub) - len(original)), frame_rate=16000)
    elif len(original) > len(dub):
        dub = dub + AudioSegment.silent(duration=(len(original) - len(dub)), frame_rate=16000)

    mixed = original.overlay(dub)
    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    mixed.export(output_wav_path, format="wav")
    return output_wav_path


def mix_audio_tracks(
    *,
    tracks: list[dict],
    output_wav_path: str,
    total_duration_ms: int | None = None,
    sample_rate: int = 16000,
) -> str:
    """Mix timeline audio tracks into one PCM WAV.

    Each track dictionary accepts ``path``, ``volume`` (percentage),
    ``muted``, ``start``/``end`` (seconds), ``source_start`` (seconds), and
    ``loop``.  This is deliberately a small, deterministic compositor used
    by both preview and export so the track-volume controls have one meaning.
    A track with volume <= 0 or muted=True is skipped without opening its
    source file.  ``loop`` is useful for music beds that are shorter than the
    video; voice/original tracks should leave it false.
    """
    _require_pydub()
    from pydub import AudioSegment

    normalized_tracks = []
    max_end_ms = max(0, int(total_duration_ms or 0))
    for raw in list(tracks or []):
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("path", "") or "").strip()
        if not path or not os.path.exists(path):
            continue
        if bool(raw.get("muted", False)):
            continue
        try:
            volume = max(0.0, min(200.0, float(raw.get("volume", 100.0))))
        except (TypeError, ValueError):
            volume = 100.0
        if volume <= 0.0:
            continue
        try:
            start_ms = max(0, int(float(raw.get("start", 0.0) or 0.0) * 1000.0))
        except (TypeError, ValueError):
            start_ms = 0
        try:
            end_ms = max(0, int(float(raw.get("end", 0.0) or 0.0) * 1000.0))
        except (TypeError, ValueError):
            end_ms = 0
        try:
            source_start_ms = max(0, int(float(raw.get("source_start", 0.0) or 0.0) * 1000.0))
        except (TypeError, ValueError):
            source_start_ms = 0
        normalized_tracks.append((raw, path, volume, start_ms, end_ms, source_start_ms))

    if not normalized_tracks:
        raise ValueError("No active audio tracks to mix.")

    rendered = []
    for raw, path, volume, start_ms, end_ms, source_start_ms in normalized_tracks:
        audio, _ = _read_audio_to_mono_float32(path, target_sr=sample_rate)
        if source_start_ms > 0:
            source_start_samples = int(source_start_ms * sample_rate / 1000)
            if source_start_samples < len(audio):
                audio = audio[source_start_samples:]
            else:
                continue
        if len(audio) == 0:
            continue

        requested_samples = int((end_ms - start_ms) * sample_rate / 1000) if end_ms > start_ms else 0
        if bool(raw.get("loop", False)) and requested_samples > len(audio) and len(audio) > 0:
            repeats = int(np.ceil(requested_samples / len(audio)))
            audio = np.tile(audio, repeats)[:requested_samples]
        elif requested_samples > 0 and len(audio) > requested_samples:
            audio = audio[:requested_samples]

        linear_gain = float(volume / 100.0)
        if abs(linear_gain - 1.0) > 0.001:
            audio = audio * linear_gain

        render_end_ms = start_ms + int(len(audio) * 1000 / sample_rate)
        max_end_ms = max(max_end_ms, render_end_ms, end_ms)
        rendered.append((start_ms, audio))

    if not rendered or max_end_ms <= 0:
        raise ValueError("Active audio tracks contain no audio.")

    total_samples = int(max_end_ms * sample_rate / 1000)
    base_buffer = np.zeros(total_samples, dtype=np.float32)

    for start_ms, audio in rendered:
        start_sample = int(start_ms * sample_rate / 1000)
        end_sample = min(total_samples, start_sample + len(audio))
        fit_len = end_sample - start_sample
        if fit_len > 0:
            base_buffer[start_sample:end_sample] += audio[:fit_len]

    # Clip output to [-1.0, 1.0] instead of whole-track normalization
    # to maintain deterministic parity with native real-time PCM preview.
    np.clip(base_buffer, -1.0, 1.0, out=base_buffer)

    os.makedirs(os.path.dirname(output_wav_path) or ".", exist_ok=True)
    sf.write(output_wav_path, base_buffer, sample_rate, subtype="PCM_16")
    return output_wav_path


def mix_pcm_block(blocks: list[np.ndarray], gains: list[float]) -> np.ndarray:
    """Mix multiple 1D float32 PCM audio blocks using linear gains and output clipping [-1.0, 1.0].

    All input blocks must have the same length. Input blocks are never mutated in-place.
    """
    if not blocks:
        return np.empty(0, dtype=np.float32)

    if len(blocks) != len(gains):
        raise ValueError(f"Count mismatch: {len(blocks)} blocks and {len(gains)} gains")

    block_len = len(blocks[0])
    for b in blocks[1:]:
        if len(b) != block_len:
            raise ValueError(f"Block length mismatch: expected {block_len}, got {len(b)}")

    out = np.zeros(block_len, dtype=np.float32)
    for block, gain in zip(blocks, gains):
        g = float(gain)
        if abs(g) < 1e-6:
            continue
        clean_block = np.nan_to_num(block, copy=True, nan=0.0, posinf=1.0, neginf=-1.0)
        out += clean_block * g

    np.clip(out, -1.0, 1.0, out=out)
    return out


def patch_audio_segment_inplace(
    output_wav_path: str,
    start_seconds: float,
    duration_seconds: float,
    new_segment_wav_path: str | None = None,
    target_sr: int = 16000,
) -> bool:
    """Patch a segment in a 16-bit PCM mono WAV file in-place without rewriting the entire file.

    - If new_segment_wav_path is None or empty: fills the duration with silence (0x00).
    - If new_segment_wav_path is provided: reads audio at target_sr, converts to 16-bit PCM,
      and writes it at the target byte offset.
    Returns True if successfully patched, False otherwise.
    """
    if not output_wav_path or not os.path.exists(output_wav_path):
        return False
    if duration_seconds <= 0 and not new_segment_wav_path:
        return False

    try:
        with open(output_wav_path, "r+b") as f:
            # Locate the 'data' chunk in the RIFF WAV header
            f.seek(12)
            data_offset = -1
            data_size = 0
            while True:
                chunk_header = f.read(8)
                if len(chunk_header) < 8:
                    break
                chunk_id = chunk_header[:4]
                chunk_len = int.from_bytes(chunk_header[4:], "little")
                if chunk_id == b"data":
                    data_offset = f.tell()
                    data_size = chunk_len
                    break
                f.seek(chunk_len, 1)

            if data_offset < 0:
                # Fallback to standard 44-byte WAV header
                data_offset = 44

            byte_offset = data_offset + int(round(max(0.0, float(start_seconds)) * target_sr)) * 2

            if new_segment_wav_path and os.path.exists(new_segment_wav_path):
                clip_data, _ = _read_audio_to_mono_float32(new_segment_wav_path, target_sr=target_sr)
                if clip_data.size > 0:
                    pcm_int16 = np.clip(clip_data * 32767.0, -32768, 32767).astype(np.int16)
                    pcm_bytes = pcm_int16.tobytes()
                else:
                    target_bytes = int(round(float(duration_seconds) * target_sr)) * 2
                    pcm_bytes = b"\x00" * max(0, target_bytes)
            else:
                target_bytes = int(round(float(duration_seconds) * target_sr)) * 2
                pcm_bytes = b"\x00" * max(0, target_bytes)

            if pcm_bytes:
                f.seek(byte_offset)
                f.write(pcm_bytes)
                f.flush()
            return True
    except Exception as exc:
        print(f"[AudioMixer] patch_audio_segment_inplace error: {exc}")
        return False


