#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""app/media_decode.py

Native media decoding and windowed audio reading without subprocess CLI calls.
Supports SoundFile for audio files (WAV, FLAC, OGG, MP3) and PyAV for video
containers (MP4, MKV, WebM) or fallback audio streams.
"""

from __future__ import annotations

import collections
import io
import math
import os
import threading
from typing import Iterator, Optional, Tuple, Union

import numpy as np
import scipy.signal
import soundfile as sf

try:
    import av
    import av.error
except ImportError:
    av = None


def _downmix_to_mono(pcm: np.ndarray) -> np.ndarray:
    """Ensure PCM array is 1D mono float32 normalized to [-1.0, 1.0]."""
    if pcm is None or len(pcm) == 0:
        return np.empty(0, dtype=np.float32)

    # Normalize integer types if any are received (e.g. int16, int32)
    if np.issubdtype(pcm.dtype, np.integer):
        max_val = float(np.iinfo(pcm.dtype).max)
        pcm = pcm.astype(np.float32) / max_val
    else:
        pcm = pcm.astype(np.float32, copy=False)

    if pcm.ndim == 1:
        return pcm
    if pcm.ndim == 2:
        # If shape is (samples, channels) e.g. SoundFile
        if pcm.shape[1] <= 8 and pcm.shape[0] >= pcm.shape[1]:
            return np.mean(pcm, axis=1, dtype=np.float32)
        # If shape is (channels, samples) e.g. PyAV planar
        if pcm.shape[0] <= 8:
            return np.mean(pcm, axis=0, dtype=np.float32)
        # Fallback average along smallest axis
        axis = 0 if pcm.shape[0] < pcm.shape[1] else 1
        return np.mean(pcm, axis=axis, dtype=np.float32)
    return pcm.flatten().astype(np.float32)


def _resample_audio(pcm: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    """Resample 1D float32 audio using polyphase filtering."""
    if orig_sr <= 0 or target_sr <= 0:
        raise ValueError(f"Invalid sample rates: orig_sr={orig_sr}, target_sr={target_sr}")
    if orig_sr == target_sr:
        return pcm.astype(np.float32, copy=False)
    if len(pcm) == 0:
        return np.empty(0, dtype=np.float32)

    gcd = math.gcd(orig_sr, target_sr)
    up = target_sr // gcd
    down = orig_sr // gcd
    resampled = scipy.signal.resample_poly(pcm, up, down).astype(np.float32)
    return resampled


def decode_audio(
    source: Union[str, bytes, os.PathLike],
    *,
    sample_rate: int = 16000,
) -> Tuple[np.ndarray, int]:
    """Decode audio from bytes or file path into a 1D mono float32 array.

    Only for individual sentences or short clips. For long tracks, use
    AudioReader to avoid loading entire files into RAM.
    """
    if source is None:
        raise ValueError("Source cannot be None")

    if isinstance(source, bytes):
        if len(source) == 0:
            raise ValueError("Empty audio source bytes")
        bio = io.BytesIO(source)
        try:
            data, sr = sf.read(bio, dtype="float32")
            mono = _downmix_to_mono(data)
            return _resample_audio(mono, sr, sample_rate), sample_rate
        except Exception as sf_err:
            bio.seek(0)
            if av is None:
                raise ValueError(f"Failed to decode audio bytes with SoundFile: {sf_err}") from sf_err
            try:
                container = av.open(bio)
                if not container.streams.audio:
                    raise ValueError("No audio streams found in container")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="fltp", rate=sample_rate)
                frames = []
                for frame in container.decode(stream):
                    for rf in resampler.resample(frame):
                        arr = rf.to_ndarray()
                        mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                        frames.append(mono)
                for rf in resampler.resample(None):
                    arr = rf.to_ndarray()
                    mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                    frames.append(mono)
                container.close()
                if not frames:
                    return np.empty(0, dtype=np.float32), sample_rate
                return np.concatenate(frames), sample_rate
            except Exception as av_err:
                raise ValueError(f"Failed to decode audio bytes: {sf_err}; {av_err}") from av_err
    else:
        path_str = os.fspath(source)
        if not os.path.exists(path_str):
            raise FileNotFoundError(f"Audio file not found: {path_str}")
        if os.path.getsize(path_str) == 0:
            raise ValueError(f"Audio file is empty: {path_str}")

        try:
            data, sr = sf.read(path_str, dtype="float32")
            mono = _downmix_to_mono(data)
            return _resample_audio(mono, sr, sample_rate), sample_rate
        except Exception as sf_err:
            if av is None:
                raise ValueError(f"Failed to open audio file {path_str} with SoundFile: {sf_err}") from sf_err
            try:
                container = av.open(path_str)
                if not container.streams.audio:
                    raise ValueError(f"No audio streams found in {path_str}")
                stream = container.streams.audio[0]
                resampler = av.AudioResampler(format="fltp", rate=sample_rate)
                frames = []
                for frame in container.decode(stream):
                    for rf in resampler.resample(frame):
                        arr = rf.to_ndarray()
                        mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                        frames.append(mono)
                for rf in resampler.resample(None):
                    arr = rf.to_ndarray()
                    mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                    frames.append(mono)
                container.close()
                if not frames:
                    return np.empty(0, dtype=np.float32), sample_rate
                return np.concatenate(frames), sample_rate
            except Exception as av_err:
                raise ValueError(f"Failed to decode audio file {path_str}: {sf_err}; {av_err}") from av_err


class AudioReader:
    """Windowed audio reader for timeline playback and mixing without loading entire files into RAM.

    Reads windows [start_sample : start_sample + sample_count] at the target sample_rate.
    Automatically pads zeros for out-of-bounds requests (negative offsets or past EOF).
    """

    def __init__(self, path: Union[str, os.PathLike], *, sample_rate: int = 16000):
        self.path = os.fspath(path)
        if not os.path.exists(self.path):
            raise FileNotFoundError(f"Audio file not found: {self.path}")

        file_stat = os.stat(self.path)
        self.file_signature = (file_stat.st_mtime_ns, file_stat.st_size)

        self.sample_rate = sample_rate
        self._is_closed = False
        self._backend: str = "soundfile"
        self._sf_file: Optional[sf.SoundFile] = None
        self._av_container = None
        self._av_stream = None

        # Attempt opening with SoundFile first
        try:
            self._sf_file = sf.SoundFile(self.path)
            self._orig_sr = self._sf_file.samplerate
            self._channels = self._sf_file.channels
            self._total_orig_frames = len(self._sf_file)
            self.total_samples = int(round(self._total_orig_frames * (self.sample_rate / self._orig_sr)))
            self._backend = "soundfile"
        except Exception:
            if av is None:
                raise ValueError(f"Cannot open {self.path} with SoundFile and PyAV is unavailable")
            # Fallback to PyAV
            self._av_container = av.open(self.path)
            if not self._av_container.streams.audio:
                self._av_container.close()
                raise ValueError(f"No audio stream found in {self.path}")
            self._av_stream = self._av_container.streams.audio[0]
            self._orig_sr = self._av_stream.codec_context.sample_rate or self.sample_rate
            self._channels = self._av_stream.codec_context.channels or 1

            if self._av_container.duration:
                dur_sec = float(self._av_container.duration / 1_000_000.0)
            elif self._av_stream.duration and self._av_stream.time_base:
                dur_sec = float(self._av_stream.duration * self._av_stream.time_base)
            else:
                dur_sec = 0.0
            self.total_samples = int(round(dur_sec * self.sample_rate))
            self._backend = "pyav"
            self._current_sample = 0
            self._av_buffer = np.empty(0, dtype=np.float32)
            self._av_buffer_start = 0
            self._av_resampler = None

    @property
    def duration_seconds(self) -> float:
        return self.total_samples / float(self.sample_rate)

    def read(self, start_sample: int, sample_count: int) -> np.ndarray:
        """Read a window of `sample_count` samples beginning at `start_sample`.

        Returns a 1D float32 numpy array of length `sample_count`.
        Pads with zeros for ranges before 0 or after EOF.
        """
        if self._is_closed:
            raise RuntimeError("AudioReader is closed")
        if sample_count <= 0:
            return np.empty(0, dtype=np.float32)

        output = np.zeros(sample_count, dtype=np.float32)

        # Out-of-bounds checks
        if start_sample >= self.total_samples or start_sample + sample_count <= 0:
            return output

        # Calculate valid in-file range
        valid_start = max(0, start_sample)
        valid_end = min(self.total_samples, start_sample + sample_count)
        valid_count = valid_end - valid_start

        if valid_count <= 0:
            return output

        out_offset = valid_start - start_sample

        if self._backend == "soundfile":
            samples = self._read_soundfile(valid_start, valid_count)
        else:
            samples = self._read_pyav(valid_start, valid_count)

        copied_count = min(len(samples), valid_count)
        output[out_offset : out_offset + copied_count] = samples[:copied_count]
        return output

    def _read_soundfile(self, start_sample: int, sample_count: int) -> np.ndarray:
        assert self._sf_file is not None

        if self._orig_sr == self.sample_rate:
            self._sf_file.seek(start_sample)
            data = self._sf_file.read(sample_count, dtype="float32")
            return _downmix_to_mono(data)

        # Scale sample positions to original sample rate
        gcd = math.gcd(self.sample_rate, self._orig_sr)
        up = self.sample_rate // gcd
        down = self._orig_sr // gcd

        orig_start = int(math.floor(start_sample * (self._orig_sr / self.sample_rate)))
        orig_end = int(math.ceil((start_sample + sample_count) * (self._orig_sr / self.sample_rate)))

        # Add filter margin to avoid FIR edge transients (Issue 5: continuous resampling)
        margin = int(math.ceil(10 * max(up, down)))
        m_start = max(0, orig_start - margin)
        m_end = min(self._total_orig_frames, orig_end + margin)
        m_count = max(0, m_end - m_start)

        if m_count <= 0:
            return np.zeros(sample_count, dtype=np.float32)

        self._sf_file.seek(m_start)
        data = self._sf_file.read(m_count, dtype="float32")
        mono = _downmix_to_mono(data)
        resampled = scipy.signal.resample_poly(mono, up, down).astype(np.float32)

        target_offset = int(round(start_sample - (m_start * self.sample_rate / self._orig_sr)))
        target_offset = max(0, target_offset)

        if target_offset < len(resampled):
            slice_data = resampled[target_offset : target_offset + sample_count]
            if len(slice_data) < sample_count:
                pad = np.zeros(sample_count - len(slice_data), dtype=np.float32)
                return np.concatenate([slice_data, pad])
            return slice_data
        return np.zeros(sample_count, dtype=np.float32)

    def _read_pyav(self, start_sample: int, sample_count: int) -> np.ndarray:
        assert self._av_container is not None and self._av_stream is not None

        buf_end = (self._av_buffer_start or 0) + len(self._av_buffer)
        if (
            self._av_buffer_start is not None
            and self._av_buffer_start <= start_sample
            and start_sample + sample_count <= buf_end
        ):
            offset = start_sample - self._av_buffer_start
            if offset > 32000:
                trim = offset - 16000
                self._av_buffer = self._av_buffer[trim:]
                self._av_buffer_start += trim
                offset -= trim
            return self._av_buffer[offset : offset + sample_count]

        # Determine target seek timestamp
        target_time_sec = max(0.0, start_sample / float(self.sample_rate))
        stream_tb = self._av_stream.time_base or (1 / self._orig_sr)
        start_pts = self._av_stream.start_time if self._av_stream.start_time is not None else 0

        needs_seek = (
            self._av_resampler is None
            or self._av_buffer_start is None
            or start_sample < self._av_buffer_start
            or start_sample > buf_end + 16000
        )
        if needs_seek:
            target_pts = start_pts + int(target_time_sec / stream_tb)
            try:
                self._av_container.seek(target_pts, stream=self._av_stream, backward=True)
            except (av.FFmpegError, OSError):
                pass
            self._av_resampler = av.AudioResampler(format="fltp", rate=self.sample_rate)
            self._av_buffer = np.empty(0, dtype=np.float32)
            self._av_buffer_start = None

        needed_end = start_sample + sample_count
        try:
            for frame in self._av_container.decode(self._av_stream):
                pts_val = (frame.pts - start_pts) if frame.pts is not None else int(round(target_time_sec / stream_tb))
                pts_s = float(pts_val * stream_tb)
                dur_s = float(frame.samples) / float(frame.sample_rate)
                frame_start_sample = int(round(pts_s * self.sample_rate))

                if self._av_buffer_start is None:
                    if pts_s + dur_s < target_time_sec - 0.05:
                        continue
                    self._av_buffer_start = frame_start_sample

                # Resample frame to self.sample_rate mono
                resampled_parts = []
                for rf in self._av_resampler.resample(frame):
                    arr = rf.to_ndarray()
                    mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                    resampled_parts.append(mono)

                if resampled_parts:
                    frame_mono = np.concatenate(resampled_parts) if len(resampled_parts) > 1 else resampled_parts[0]
                    current_end = self._av_buffer_start + len(self._av_buffer)
                    gap_samples = frame_start_sample - current_end
                    if gap_samples > 0:
                        silence = np.zeros(gap_samples, dtype=np.float32)
                        self._av_buffer = np.concatenate([self._av_buffer, silence]) if len(self._av_buffer) else silence
                    self._av_buffer = np.concatenate([self._av_buffer, frame_mono]) if len(self._av_buffer) else frame_mono

                if self._av_buffer_start is not None and self._av_buffer_start + len(self._av_buffer) >= needed_end:
                    break
            else:
                # End of stream reached: flush remaining buffered samples from resampler
                if self._av_resampler is not None:
                    try:
                        for rf in self._av_resampler.resample(None):
                            arr = rf.to_ndarray()
                            mono = np.mean(arr, axis=0, dtype=np.float32) if arr.ndim == 2 else arr.flatten().astype(np.float32)
                            self._av_buffer = np.concatenate([self._av_buffer, mono]) if len(self._av_buffer) else mono
                    except (EOFError, StopIteration, av.FFmpegError):
                        pass
        except (EOFError, StopIteration, av.FFmpegError):
            pass

        if self._av_buffer_start is None:
            self._av_buffer_start = start_sample

        if len(self._av_buffer) == 0:
            return np.zeros(sample_count, dtype=np.float32)

        offset = start_sample - self._av_buffer_start
        if offset < 0:
            pad_len = -offset
            valid_len = min(len(self._av_buffer), sample_count - pad_len)
            valid = self._av_buffer[:valid_len] if valid_len > 0 else np.empty(0, dtype=np.float32)
            res = np.concatenate([np.zeros(pad_len, dtype=np.float32), valid])
            if len(res) < sample_count:
                res = np.concatenate([res, np.zeros(sample_count - len(res), dtype=np.float32)])
            return res[:sample_count]

        slice_data = self._av_buffer[offset : offset + sample_count]
        if len(slice_data) < sample_count:
            pad = np.zeros(sample_count - len(slice_data), dtype=np.float32)
            return np.concatenate([slice_data, pad])
        return slice_data

    def close(self) -> None:
        """Release container and underlying file handles."""
        self._is_closed = True
        self._av_buffer = np.empty(0, dtype=np.float32)
        self._av_resampler = None
        if self._sf_file is not None:
            try:
                self._sf_file.close()
            except OSError:
                pass
            self._sf_file = None

        if self._av_container is not None:
            try:
                self._av_container.close()
            except (OSError, av.FFmpegError):
                pass
            self._av_container = None
            self._av_stream = None

    def __enter__(self) -> AudioReader:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


def convert_audio_to_wav_16k_mono(
    source: Union[str, bytes, bytearray, io.BytesIO, os.PathLike],
    wav_path: str,
) -> str:
    """Convert audio source (file path, bytes, or BytesIO) to 16kHz mono WAV atomically."""
    from app.tts_processor import convert_audio_data_to_wav_16k_mono
    return convert_audio_data_to_wav_16k_mono(source, wav_path)


WAVEFORM_ALGO_VERSION = 1


class BoundedWaveformCache:
    """Bounded in-memory LRU cache for waveform envelopes with version and fingerprinting."""

    def __init__(self, max_bytes: int = 16 * 1024 * 1024, max_entries: int = 128):
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.current_bytes = 0
        self._cache: collections.OrderedDict[tuple, tuple[list[float], float]] = collections.OrderedDict()
        self._lock = threading.Lock()

    @staticmethod
    def compute_fingerprint(
        path: str,
        stat: os.stat_result,
        bucket_count: int,
        stream_index: int = 0,
    ) -> tuple:
        return (
            WAVEFORM_ALGO_VERSION,
            os.path.abspath(path),
            int(stat.st_size),
            int(getattr(stat, "st_mtime_ns", 0)),
            int(stream_index),
            int(bucket_count),
        )

    def get(self, key: tuple) -> Optional[tuple[list[float], float]]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: tuple, value: tuple[list[float], float]) -> None:
        waveform, _dur = value
        entry_bytes = len(waveform) * 8 + 128
        with self._lock:
            if key in self._cache:
                old_wf, _ = self._cache.pop(key)
                self.current_bytes -= (len(old_wf) * 8 + 128)

            while (
                (self.current_bytes + entry_bytes > self.max_bytes or len(self._cache) >= self.max_entries)
                and self._cache
            ):
                _old_key, (old_wf, _) = self._cache.popitem(last=False)
                self.current_bytes -= (len(old_wf) * 8 + 128)

            self._cache[key] = value
            self.current_bytes += entry_bytes

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self.current_bytes = 0


_WAVEFORM_CACHE = BoundedWaveformCache()


def has_audio_stream(path: Union[str, os.PathLike]) -> Optional[bool]:
    """Check if a media file contains at least one decodable audio stream.

    Returns:
        True: Confirmed to contain >= 1 audio stream.
        False: Successfully opened and confirmed to have 0 audio streams.
        None: Probe failed, container unopenable, or PyAV unavailable.
    """
    path_str = os.fspath(path)
    if not path_str or not os.path.exists(path_str):
        return None
    try:
        import soundfile as sf
        with sf.SoundFile(path_str) as f:
            if f.channels > 0 and len(f) >= 0:
                return True
    except Exception:
        pass
    if av is not None:
        try:
            with av.open(path_str) as container:
                return len(container.streams.audio) > 0
        except Exception:
            return None
    return None


def iter_video_thumbnails(
    path: str,
    timestamps: list[float],
    *,
    width: int = 180,
    max_thumbnails: int = 120,
    max_bytes: int = 8 * 1024 * 1024,
) -> Iterator[Tuple[float, np.ndarray]]:
    """Yield (actual_pts_seconds, rgb_ndarray) for requested video timestamps.

    Decodes video in-process using PyAV without subprocess CLI calls.
    Output RGB array has shape (h, width, 3) and dtype uint8.
    Compensates for non-zero stream.start_time and bounds output memory to max_bytes.
    """
    if not timestamps or not path or not os.path.exists(path):
        return

    if av is None:
        raise RuntimeError("PyAV is required for iter_video_thumbnails")

    # Bound requested timestamp list to max_thumbnails
    if len(timestamps) > max_thumbnails:
        step = len(timestamps) / float(max_thumbnails)
        timestamps = [timestamps[int(i * step)] for i in range(max_thumbnails)]

    container = None
    try:
        container = av.open(path)
        if not container.streams.video:
            return
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        tb = float(stream.time_base) if stream.time_base is not None else 1.0 / 1000.0

        start_pts = stream.start_time if stream.start_time is not None else 0
        if start_pts == 0 and container.start_time is not None and container.start_time > 0:
            start_pts = int(round((container.start_time / 1_000_000.0) / tb))

        orig_w = stream.codec_context.width or 180
        orig_h = stream.codec_context.height or 135
        target_w = max(2, (int(width) // 2) * 2)
        target_h = max(2, (int(round(target_w * orig_h / max(1, orig_w))) // 2) * 2)

        current_frame = None
        current_rel_time = -1.0
        frame_iter = container.decode(stream)
        yielded_bytes = 0

        for target in timestamps:
            target_s = max(0.0, float(target))
            if current_rel_time < 0.0 or target_s < current_rel_time or (target_s - current_rel_time) > 1.5:
                seek_pts = start_pts + int(round(target_s / tb))
                try:
                    container.seek(seek_pts, stream=stream, backward=True)
                    frame_iter = container.decode(stream)
                except (av.FFmpegError, OSError):
                    pass

            target_frame = None
            for frame in frame_iter:
                f_pts = frame.pts if frame.pts is not None else (
                    int(round(frame.time / tb)) if frame.time is not None else (start_pts + int(round(target_s / tb)))
                )
                rel_pts = f_pts - start_pts
                ft = float(rel_pts * tb)
                current_frame = frame
                current_rel_time = ft
                if ft >= target_s - 0.04:
                    target_frame = frame
                    break

            if target_frame is None and current_frame is not None:
                target_frame = current_frame

            if target_frame is not None:
                tf_pts = target_frame.pts if target_frame.pts is not None else (
                    int(round(target_frame.time / tb)) if target_frame.time is not None else (start_pts + int(round(target_s / tb)))
                )
                actual_pts = max(0.0, float((tf_pts - start_pts) * tb))
                rf = target_frame.reformat(width=target_w, height=target_h, format="rgb24")
                arr = np.ascontiguousarray(rf.to_ndarray())

                frame_bytes = int(arr.nbytes)
                if yielded_bytes + frame_bytes > max_bytes:
                    break
                yielded_bytes += frame_bytes
                yield actual_pts, arr
    finally:
        if container is not None:
            try:
                container.close()
            except (av.FFmpegError, OSError):
                pass


def build_waveform(
    path: str,
    *,
    bucket_count: int = 1200,
) -> Tuple[list[float], float]:
    """Build audio waveform envelope without decoding full PCM array into RAM.

    Streams PCM chunks using AudioReader and computes bucket peak/RMS envelope.
    Returns (waveform_envelope, duration_seconds).
    """
    if not path or not os.path.exists(path):
        return [], 0.0

    stat = os.stat(path)
    cache_key = BoundedWaveformCache.compute_fingerprint(path, stat, bucket_count)
    cached = _WAVEFORM_CACHE.get(cache_key)
    if cached is not None:
        return cached

    # 1. Fast path: direct SoundFile reading for WAV, FLAC, OGG, etc.
    try:
        import soundfile as sf
        with sf.SoundFile(path) as sf_file:
            total_samples = len(sf_file)
            sr = sf_file.samplerate
            duration_s = total_samples / float(sr) if sr > 0 else 0.0
            if total_samples <= 0 or duration_s <= 0.0:
                result = ([], duration_s)
                _WAVEFORM_CACHE.put(cache_key, result)
                return result

            actual_buckets = int(min(bucket_count, max(240, round(duration_s * 12.0))))
            chunk_size = max(256, int(np.ceil(total_samples / max(1, actual_buckets))))
            num_buckets = int(np.ceil(total_samples / chunk_size))

            bucket_peaks = np.zeros(num_buckets, dtype=np.float32)
            bucket_sum_sq = np.zeros(num_buckets, dtype=np.float64)
            bucket_counts = np.zeros(num_buckets, dtype=np.int64)

            block_size = max(chunk_size, 65536)
            global_peak = 0.0
            offset = 0

            while offset < total_samples:
                count = min(block_size, total_samples - offset)
                block = sf_file.read(count, dtype="float32")
                if len(block) == 0:
                    break
                if block.ndim > 1:
                    block = _downmix_to_mono(block)

                abs_block = np.abs(block)
                block_peak = float(np.max(abs_block)) if len(abs_block) > 0 else 0.0
                if block_peak > global_peak:
                    global_peak = block_peak

                sample_indices = offset + np.arange(len(block))
                bucket_indices = np.minimum(num_buckets - 1, sample_indices // chunk_size)

                np.maximum.at(bucket_peaks, bucket_indices, abs_block)
                np.add.at(bucket_sum_sq, bucket_indices, block.astype(np.float64) ** 2)
                np.add.at(bucket_counts, bucket_indices, 1)

                offset += count

            if global_peak <= 0.0:
                result = ([0.0] * num_buckets, duration_s)
                _WAVEFORM_CACHE.put(cache_key, result)
                return result

            safe_counts = np.maximum(1, bucket_counts)
            norm_peaks = bucket_peaks / global_peak
            rms = np.sqrt(bucket_sum_sq / safe_counts) / global_peak

            values = np.maximum(norm_peaks, rms * 1.15)
            envelope = np.clip(values ** 0.85, 0.03, 1.0)
            waveform = [float(x) for x in envelope]

            result = (waveform, duration_s)
            _WAVEFORM_CACHE.put(cache_key, result)
            return result
    except Exception:
        # Not an audio file SoundFile can open (e.g. video container), proceed to PyAV streaming
        pass

    # 2. Fast path: linear PyAV stream decoding without resampling or seek churn
    if av is not None:
        try:
            container = av.open(path)
            if not container.streams.audio:
                dur_s = float(container.duration) / 1000000.0 if container.duration is not None else 0.0
                container.close()
                result = ([], dur_s)
                _WAVEFORM_CACHE.put(cache_key, result)
                return result

            astream = container.streams.audio[0]
            if astream.duration and astream.time_base:
                duration_s = float(astream.duration * astream.time_base)
            elif container.duration:
                duration_s = float(container.duration) / 1000000.0
            else:
                duration_s = 0.0

            sr = astream.rate or 48000
            total_samples = int(round(duration_s * sr)) if duration_s > 0 else 0
            actual_buckets = int(min(bucket_count, max(240, round(duration_s * 12.0)))) if duration_s > 0 else bucket_count
            actual_buckets = max(1, actual_buckets)
            chunk_size = max(256, int(np.ceil(total_samples / max(1, actual_buckets)))) if total_samples > 0 else 256
            num_buckets = int(np.ceil(total_samples / chunk_size)) if total_samples > 0 else actual_buckets
            num_buckets = max(actual_buckets, num_buckets)

            bucket_peaks = np.zeros(num_buckets, dtype=np.float32)
            bucket_sum_sq = np.zeros(num_buckets, dtype=np.float64)
            bucket_counts = np.zeros(num_buckets, dtype=np.int64)

            global_peak = 0.0
            sample_offset = 0

            for frame in container.decode(astream):
                arr = frame.to_ndarray()
                if arr.ndim == 2:
                    arr = np.mean(arr, axis=0, dtype=np.float32)
                else:
                    arr = arr.flatten().astype(np.float32)

                n = len(arr)
                if n == 0:
                    continue

                abs_arr = np.abs(arr)
                pk = float(np.max(abs_arr))
                if pk > global_peak:
                    global_peak = pk

                s_indices = sample_offset + np.arange(n)
                b_indices = np.minimum(num_buckets - 1, s_indices // chunk_size)

                np.maximum.at(bucket_peaks, b_indices, abs_arr)
                np.add.at(bucket_sum_sq, b_indices, arr.astype(np.float64) ** 2)
                np.add.at(bucket_counts, b_indices, 1)

                sample_offset += n

            container.close()

            if sample_offset > 0 and duration_s <= 0.0:
                duration_s = sample_offset / float(sr)

            if global_peak <= 0.0:
                result = ([0.0] * num_buckets, duration_s)
                _WAVEFORM_CACHE.put(cache_key, result)
                return result

            safe_counts = np.maximum(1, bucket_counts)
            norm_peaks = bucket_peaks / global_peak
            rms = np.sqrt(bucket_sum_sq / safe_counts) / global_peak

            values = np.maximum(norm_peaks, rms * 1.15)
            envelope = np.clip(values ** 0.85, 0.03, 1.0)
            waveform = [float(x) for x in envelope]

            result = (waveform, duration_s)
            _WAVEFORM_CACHE.put(cache_key, result)
            return result
        except Exception:
            pass

    # 3. Fallback: AudioReader windowed stream
    try:
        reader = AudioReader(path, sample_rate=16000)
    except Exception:
        duration_s = 0.0
        if av is not None:
            try:
                c = av.open(path)
                duration_s = float(c.duration) / 1000000.0 if c.duration is not None else 0.0
                c.close()
            except Exception:
                pass
        result = ([], duration_s)
        _WAVEFORM_CACHE.put(cache_key, result)
        return result

    with reader:
        total_samples = reader.total_samples
        duration_s = reader.duration_seconds
        if total_samples <= 0 or duration_s <= 0.0:
            result = ([], duration_s)
            _WAVEFORM_CACHE.put(cache_key, result)
            return result

        actual_buckets = int(min(bucket_count, max(240, round(duration_s * 12.0))))
        chunk_size = max(256, int(np.ceil(total_samples / max(1, actual_buckets))))
        num_buckets = int(np.ceil(total_samples / chunk_size))

        bucket_peaks = np.zeros(num_buckets, dtype=np.float32)
        bucket_sum_sq = np.zeros(num_buckets, dtype=np.float64)
        bucket_counts = np.zeros(num_buckets, dtype=np.int64)

        block_size = 16384
        global_peak = 0.0
        offset = 0

        while offset < total_samples:
            count = min(block_size, total_samples - offset)
            block = reader.read(offset, count)
            if len(block) == 0:
                break

            abs_block = np.abs(block)
            block_peak = float(np.max(abs_block)) if len(abs_block) > 0 else 0.0
            if block_peak > global_peak:
                global_peak = block_peak

            sample_indices = offset + np.arange(len(block))
            bucket_indices = np.minimum(num_buckets - 1, sample_indices // chunk_size)

            np.maximum.at(bucket_peaks, bucket_indices, abs_block)
            np.add.at(bucket_sum_sq, bucket_indices, block.astype(np.float64) ** 2)
            np.add.at(bucket_counts, bucket_indices, 1)

            offset += count

        if global_peak <= 0.0:
            result = ([0.0] * num_buckets, duration_s)
            _WAVEFORM_CACHE.put(cache_key, result)
            return result

        safe_counts = np.maximum(1, bucket_counts)
        norm_peaks = bucket_peaks / global_peak
        rms = np.sqrt(bucket_sum_sq / safe_counts) / global_peak

        values = np.maximum(norm_peaks, rms * 1.15)
        envelope = np.clip(values ** 0.85, 0.03, 1.0)
        waveform = [float(x) for x in envelope]

        result = (waveform, duration_s)
        _WAVEFORM_CACHE.put(cache_key, result)
        return result

