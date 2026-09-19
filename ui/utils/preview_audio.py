#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui/utils/preview_audio.py

Real-time PCM preview audio engine using PySide6 QAudioSink, windowed AudioReader,
and in-memory block mixing. Eliminates intermediate WAV files and FFmpeg CLI calls
during timeline preview, volume adjustments, and seeking.
"""

from __future__ import annotations

from collections import OrderedDict
import math
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import scipy.signal

from PySide6.QtCore import (
    QCoreApplication,
    QMetaObject,
    QObject,
    QThread,
    QTimer,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtMultimedia import (
    QAudio,
    QAudioFormat,
    QAudioSink,
    QMediaDevices,
)

from app.audio_mixer import mix_pcm_block
from app.media_decode import AudioReader
from app.services.time_warp_service import TimeWarpService

try:
    import av
    import av.filter
except ImportError:
    av = None


class _LRUPcmCache:
    """Memory-bounded LRU cache for 16kHz mono float32 PCM blocks."""

    def __init__(self, max_bytes: int = 128 * 1024 * 1024):
        self.max_bytes = max_bytes
        self._cache: OrderedDict[Tuple[Any, ...], np.ndarray] = OrderedDict()
        self._current_bytes: int = 0

    def get(self, key: Tuple[Any, ...]) -> Optional[np.ndarray]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: Tuple[Any, ...], block: np.ndarray) -> None:
        block_bytes = block.nbytes
        if block_bytes > self.max_bytes:
            return  # single block exceeds cache limit

        if key in self._cache:
            self._current_bytes -= self._cache[key].nbytes
            del self._cache[key]

        while self._current_bytes + block_bytes > self.max_bytes and self._cache:
            _, old_block = self._cache.popitem(last=False)
            self._current_bytes -= old_block.nbytes

        self._cache[key] = block
        self._current_bytes += block_bytes

    def clear(self) -> None:
        self._cache.clear()
        self._current_bytes = 0


class _PreviewAudioWorker(QObject):
    """Internal audio engine worker running on a dedicated QThread."""

    positionChanged = Signal(int)
    stateChanged = Signal(int)  # 0: Stopped, 1: Playing, 2: Paused
    errorOccurred = Signal(str)
    sinkReady = Signal(bool)

    def __init__(self, sample_rate: int = 16000, block_size: int = 160):
        super().__init__()
        self.internal_sr = sample_rate
        self.block_size = block_size  # 160 samples = 10ms at 16kHz
        self.block_duration_ms = int(round(block_size * 1000.0 / sample_rate))

        self._sink: Optional[QAudioSink] = None
        self._io_device = None
        self._output_format: Optional[QAudioFormat] = None
        self._timer: Optional[QTimer] = None

        self._tracks: List[Dict[str, Any]] = []
        self._readers: Dict[str, AudioReader] = {}
        self._warps: List[Dict[str, Any]] = []

        self._pcm_cache = _LRUPcmCache(max_bytes=128 * 1024 * 1024)

        self._timeline_pos_exact_ms: float = 0.0
        self._media_read_sample: int = 0
        self._is_playing: bool = False
        self._playback_rate: float = 1.0
        self._generation_id: int = 0
        self._tempo_graph: Any = None
        self._tempo_fifo: np.ndarray = np.empty(0, dtype=np.float32)
        self._tempo_in_pts: int = 0
        self._pending_write_bytes: bytes = b""

        # Resampling state for output sink
        self._sink_sr: int = 48000
        self._sink_channels: int = 2
        self._sink_is_float: bool = True
        self._sink_resampler: Any = None

    @property
    def _timeline_pos_ms(self) -> int:
        if self._is_playing and isinstance(self._sink, QAudioSink):
            try:
                buf_size = self._sink.bufferSize()
                if buf_size > 0:
                    free_bytes = self._sink.bytesFree()
                    buffered_bytes = max(0, buf_size - free_bytes) + len(self._pending_write_bytes)
                    bytes_per_sample = 4 if self._sink_is_float else 2
                    bytes_per_frame = bytes_per_sample * self._sink_channels
                    buffered_ms = (buffered_bytes / bytes_per_frame) * 1000.0 / self._sink_sr
                    audible_ms = self._timeline_pos_exact_ms - (buffered_ms * self._playback_rate)
                    return max(0, int(round(audible_ms)))
            except Exception:
                pass
        return int(round(self._timeline_pos_exact_ms))

    @_timeline_pos_ms.setter
    def _timeline_pos_ms(self, val: Any) -> None:
        self._timeline_pos_exact_ms = float(val)

    @Slot()
    def init_sink(self) -> None:
        """Initialize QAudioSink with default audio output device format."""
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                self.sinkReady.emit(False)
                self.errorOccurred.emit("No default audio output device available")
                return

            pref = device.preferredFormat()
            fmt = QAudioFormat()
            self._sink_sr = pref.sampleRate() if pref.sampleRate() > 0 else 48000
            fmt.setSampleRate(self._sink_sr)

            self._sink_channels = pref.channelCount() if pref.channelCount() > 0 else 2
            fmt.setChannelCount(self._sink_channels)

            if pref.sampleFormat() == QAudioFormat.SampleFormat.Float:
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
                self._sink_is_float = True
            else:
                fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
                self._sink_is_float = False

            self._output_format = fmt
            self._sink = QAudioSink(device, fmt, self)
            # Use ~200ms output buffer to prevent underruns on Windows while keeping instant pause/seek response
            bytes_per_sample = 4 if self._sink_is_float else 2
            bytes_per_frame = bytes_per_sample * self._sink_channels
            buffer_target = int(self._sink_sr * bytes_per_frame * 0.20)
            self._sink.setBufferSize(max(buffer_target, 16384))

            # Match internal processing sample rate to sink native rate to avoid lossy downsampling/upsampling
            self.internal_sr = self._sink_sr
            self.block_size = int(round(self._sink_sr * 0.01))  # 10ms block (e.g. 480 at 48k)
            self.block_duration_ms = int(round(self.block_size * 1000.0 / self.internal_sr))

            # If tracks were configured prior to sink initialization, reopen readers with native sample rate
            if self._tracks and self._readers:
                self._pcm_cache.clear()
                for p, reader in list(self._readers.items()):
                    try:
                        reader.close()
                    except Exception:
                        pass
                self._readers = {
                    t["path"]: AudioReader(t["path"], sample_rate=self.internal_sr)
                    for t in self._tracks
                    if os.path.exists(t["path"])
                }

            self._io_device = self._sink.start()

            self._timer = QTimer(self)
            self._timer.setInterval(self.block_duration_ms)
            self._timer.timeout.connect(self._on_timer_tick)
            self._sink_resampler = None
            self.sinkReady.emit(True)
        except Exception as exc:
            self.sinkReady.emit(False)
            self.errorOccurred.emit(f"Failed to initialize audio sink: {exc}")

    @Slot(list, list)
    def set_tracks(self, tracks: List[Dict[str, Any]], warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update active audio tracks and timeline warp markers."""
        self._warps = list(warps or [])
        new_tracks = []
        needed_paths = set()

        for raw in tracks:
            path = str(raw.get("path", "")).strip()
            if not path or not os.path.exists(path):
                continue

            track_id = str(raw.get("id") or path)
            needed_paths.add(path)

            vol = float(raw.get("volume", 100.0))
            muted = bool(raw.get("muted", False))
            target_gain = 0.0 if muted else max(0.0, min(2.0, vol / 100.0))

            # Maintain existing smooth gain if track was already present
            existing = next((t for t in self._tracks if t["id"] == track_id), None)
            cur_gain = existing["current_gain"] if existing else target_gain

            new_tracks.append({
                "id": track_id,
                "path": path,
                "start_ms": max(0, int(float(raw.get("start", 0.0)) * 1000.0)),
                "end_ms": max(0, int(float(raw.get("end", 0.0)) * 1000.0)),
                "source_start_ms": max(0, int(float(raw.get("source_start", 0.0)) * 1000.0)),
                "volume": vol,
                "muted": muted,
                "target_gain": target_gain,
                "current_gain": cur_gain,
                "loop": bool(raw.get("loop", False)),
                "is_original_video": bool(raw.get("is_original_video", False)),
            })

        # Close readers no longer needed
        stale_paths = set(self._readers.keys()) - needed_paths
        for p in stale_paths:
            try:
                self._readers[p].close()
            except Exception:
                pass
            del self._readers[p]

        # Ensure all active tracks have open readers
        for p in needed_paths:
            if p not in self._readers:
                try:
                    self._readers[p] = AudioReader(p, sample_rate=self.internal_sr)
                except Exception as exc:
                    self.errorOccurred.emit(f"Failed to open AudioReader for {p}: {exc}")

        self._tracks = new_tracks
        self._generation_id += 1

    @Slot(str, float, bool)
    def set_track_gain(self, track_id: str, gain: float, muted: bool) -> None:
        """Update gain and mute state for a track with smooth 5ms ramping."""
        for t in self._tracks:
            if t["id"] == track_id or t["path"] == track_id:
                t["muted"] = muted
                t["volume"] = gain * 100.0
                t["target_gain"] = 0.0 if muted else max(0.0, min(2.0, gain))
                break

    @Slot(int)
    def seek(self, timeline_ms: int) -> None:
        """Seek playback to timeline millisecond."""
        self._timeline_pos_ms = max(0, int(timeline_ms))
        self._timeline_pos_exact_ms = float(self._timeline_pos_ms)
        self._media_read_sample = int(self._timeline_pos_ms * self.internal_sr / 1000.0)
        self._generation_id += 1
        self._pending_write_bytes = b""
        self._tempo_fifo = np.empty(0, dtype=np.float32)
        self._tempo_graph = None
        self._tempo_in_pts = 0
        self._sink_resampler = None
        if self._sink:
            self._sink.reset()
            if self._is_playing:
                self._io_device = self._sink.start()
        self.positionChanged.emit(self._timeline_pos_ms)

    @Slot()
    def play(self) -> None:
        """Start audio playback."""
        if not self._is_playing:
            self._is_playing = True
            if self._sink and (self._sink.state() == QAudio.StoppedState or self._io_device is None or self._sink.bytesFree() <= 0):
                self._io_device = self._sink.start()
            if self._timer and not self._timer.isActive():
                self._timer.start()
            self.stateChanged.emit(1)

    @Slot()
    def pause(self) -> None:
        """Pause audio playback."""
        if self._is_playing:
            audible_pos = self._timeline_pos_ms
            self._is_playing = False
            if self._timer and self._timer.isActive():
                self._timer.stop()
            self._pending_write_bytes = b""
            self._tempo_fifo = np.empty(0, dtype=np.float32)
            self._tempo_graph = None
            self._tempo_in_pts = 0
            self._sink_resampler = None
            if self._sink:
                self._sink.reset()
            self._io_device = None
            self._timeline_pos_exact_ms = float(audible_pos)
            self._media_read_sample = int(round(audible_pos * self.internal_sr / 1000.0))
            self.positionChanged.emit(audible_pos)
            self.stateChanged.emit(2)

    @Slot()
    def stop(self) -> None:
        """Stop audio playback and return to start."""
        self._is_playing = False
        if self._timer and self._timer.isActive():
            self._timer.stop()
        self._timeline_pos_ms = 0
        self._timeline_pos_exact_ms = 0.0
        self._media_read_sample = 0
        self._pending_write_bytes = b""
        self._tempo_fifo = np.empty(0, dtype=np.float32)
        self._tempo_graph = None
        self._tempo_in_pts = 0
        self._sink_resampler = None
        if self._sink:
            self._sink.reset()
        self._io_device = None
        self.positionChanged.emit(0)
        self.stateChanged.emit(0)

    @Slot(float)
    def set_rate(self, rate: float) -> None:
        """Set playback rate (e.g. 1.0, 1.25)."""
        new_rate = max(0.25, min(4.0, float(rate)))
        if abs(new_rate - self._playback_rate) > 1e-3:
            self._playback_rate = new_rate
            self._tempo_graph = None
            self._tempo_fifo = np.empty(0, dtype=np.float32)
            self._tempo_in_pts = 0
            self._sink_resampler = None
            self._pending_write_bytes = b""

    @Slot(list)
    def set_warps(self, warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update timeline warp markers."""
        self._warps = list(warps or [])
        self._generation_id += 1

    def _is_time_frozen(self, t_sec: float) -> bool:
        """Return True if timestamp t_sec falls within a freeze frame warp."""
        accum = 0.0
        for w in sorted(self._warps, key=lambda x: float(x.get("media_start", x.get("time", 0.0)))):
            w_type = w.get("type", "freeze")
            dur = float(w.get("duration", 0.0))
            if w_type != "freeze":
                accum += dur
                continue
            start = float(w.get("time", 0.0)) + accum
            if start <= t_sec < (start + dur):
                return True
            accum += dur
        return False

    def _read_mixed_block(self, cur_sample: int) -> np.ndarray:
        """Read and mix tracks for a window of self.block_size starting at cur_sample."""
        cur_t_sec = cur_sample / float(self.internal_sr)
        cur_timeline_pos_ms = int(round(cur_sample * 1000.0 / self.internal_sr))

        blocks: List[np.ndarray] = []
        gains: List[float] = []

        is_frozen = self._is_time_frozen(cur_t_sec)

        for track in self._tracks:
            reader = self._readers.get(track["path"])
            if reader is None:
                continue

            # Original video audio is silenced during freeze frames
            if is_frozen and track["is_original_video"]:
                continue

            start_ms = track["start_ms"]
            end_ms = track["end_ms"]

            if cur_timeline_pos_ms < start_ms:
                continue
            if end_ms > start_ms and cur_timeline_pos_ms >= end_ms:
                continue

            # Compute track-relative sample offset
            if track["is_original_video"]:
                t0_sec = cur_t_sec
                t1_sec = cur_t_sec + float(self.block_size) / float(self.internal_sr)
                m0_sec = TimeWarpService.timeline_to_media_time(t0_sec, self._warps)
                m1_sec = TimeWarpService.timeline_to_media_time(t1_sec, self._warps)

                src_offset_s = track["source_start_ms"] / 1000.0
                sample_start = int(round((m0_sec + src_offset_s) * self.internal_sr))
                sample_end = int(round((m1_sec + src_offset_s) * self.internal_sr))
                num_src_samples = max(0, sample_end - sample_start)

                cache_key = (track["path"], sample_start, num_src_samples, self.block_size, self._generation_id)
                block = self._pcm_cache.get(cache_key)
                if block is None:
                    if num_src_samples == self.block_size:
                        block = reader.read(sample_start, self.block_size)
                    elif num_src_samples <= 0:
                        block = np.zeros(self.block_size, dtype=np.float32)
                    else:
                        raw = reader.read(sample_start, num_src_samples)
                        if len(raw) == 0:
                            block = np.zeros(self.block_size, dtype=np.float32)
                        elif len(raw) == 1:
                            block = np.full(self.block_size, raw[0], dtype=np.float32)
                        else:
                            x_src = np.linspace(0.0, 1.0, len(raw), endpoint=True)
                            x_dst = np.linspace(0.0, 1.0, self.block_size, endpoint=True)
                            block = np.interp(x_dst, x_src, raw).astype(np.float32)
                    self._pcm_cache.put(cache_key, block)
            else:
                offset_ms = cur_timeline_pos_ms - start_ms + track["source_start_ms"]
                track_sample = int(offset_ms * self.internal_sr / 1000.0)

                # Check LRU cache
                cache_key = (track["path"], track_sample, self.block_size, self._generation_id)
                block = self._pcm_cache.get(cache_key)
                if block is None:
                    if track.get("loop", False) and reader.total_samples > 0:
                        sample_in_loop = track_sample % reader.total_samples
                        if sample_in_loop + self.block_size > reader.total_samples:
                            first_len = reader.total_samples - sample_in_loop
                            p1 = reader.read(sample_in_loop, first_len)
                            p2 = reader.read(0, self.block_size - first_len)
                            block = np.concatenate([p1, p2])
                        else:
                            block = reader.read(sample_in_loop, self.block_size)
                    else:
                        block = reader.read(track_sample, self.block_size)
                    self._pcm_cache.put(cache_key, block)

            # Apply smooth 5ms ramp toward target gain
            ramp_len = min(max(1, int(round(self.internal_sr * 0.005))), self.block_size)
            cur_g = track["current_gain"]
            target_g = track["target_gain"]

            if abs(cur_g - target_g) > 1e-4:
                gain_envelope = np.linspace(cur_g, target_g, ramp_len, dtype=np.float32)
                if ramp_len < self.block_size:
                    gain_envelope = np.concatenate([gain_envelope, np.full(self.block_size - ramp_len, target_g, dtype=np.float32)])
                track["current_gain"] = target_g
                block = block * gain_envelope
                gains.append(1.0)
            else:
                track["current_gain"] = target_g
                gains.append(target_g)

            blocks.append(block)

        if blocks:
            return mix_pcm_block(blocks, gains)
        return np.zeros(self.block_size, dtype=np.float32)

    def _on_timer_tick(self) -> None:
        """Mix and push PCM audio blocks to the sink."""
        if not self._is_playing or self._io_device is None or self._sink is None:
            return

        bytes_per_sample = 4 if self._sink_is_float else 2
        bytes_per_frame = bytes_per_sample * self._sink_channels

        # 1. Flush any pending leftover bytes from previous tick first
        if self._pending_write_bytes:
            bytes_written = self._io_device.write(self._pending_write_bytes)
            if bytes_written > 0:
                frames_written = bytes_written // bytes_per_frame
                ms_written = frames_written * 1000.0 / self._sink_sr
                self._timeline_pos_exact_ms += ms_written * self._playback_rate
                self.positionChanged.emit(self._timeline_pos_ms)
                self._pending_write_bytes = self._pending_write_bytes[bytes_written:]
            return

        gcd = math.gcd(self.internal_sr, self._sink_sr)
        up = self._sink_sr // gcd
        down = self.internal_sr // gcd
        out_samples_per_block = int(round(self.block_size * up / down))
        out_bytes_per_block = out_samples_per_block * bytes_per_frame

        is_real_sink = isinstance(self._sink, QAudioSink)
        target_buffer_bytes = int(self._sink_sr * bytes_per_frame * 0.20) if is_real_sink else 0
        max_blocks_per_tick = 12 if is_real_sink else 1

        blocks_written = 0
        while blocks_written < max_blocks_per_tick and self._is_playing:
            free_bytes = self._sink.bytesFree()
            if free_bytes < out_bytes_per_block:
                break

            if is_real_sink and target_buffer_bytes > 0:
                buf_size = self._sink.bufferSize()
                buffered_bytes = max(0, buf_size - free_bytes)
                if buffered_bytes >= target_buffer_bytes:
                    break

            # Keep media read sample aligned to write cursor when playing 1.0x
            if abs(self._playback_rate - 1.0) <= 0.01:
                self._media_read_sample = int(round(self._timeline_pos_exact_ms * self.internal_sr / 1000.0))

            # 2. Generate audio block (applying atempo filter if rate != 1.0)
            if abs(self._playback_rate - 1.0) > 0.01 and av is not None:
                try:
                    if getattr(self, "_tempo_graph", None) is None:
                        g = av.filter.Graph()
                        src = g.add_abuffer(format='flt', sample_rate=self.internal_sr, layout='mono', time_base=f'1/{self.internal_sr}')
                        tempo = g.add('atempo', f'{self._playback_rate:.4f}')
                        sink = g.add('abuffersink')
                        src.link_to(tempo)
                        tempo.link_to(sink)
                        g.configure()
                        self._tempo_graph = (g, src, sink)
                        self._tempo_in_pts = 0
                    _, t_src, t_sink = self._tempo_graph

                    max_feed_iterations = 20
                    iter_count = 0
                    while len(self._tempo_fifo) < self.block_size and iter_count < max_feed_iterations:
                        iter_count += 1
                        chunk = self._read_mixed_block(self._media_read_sample)
                        self._media_read_sample += len(chunk)
                        f = av.AudioFrame(format='flt', layout='mono', samples=len(chunk))
                        f.sample_rate = self.internal_sr
                        f.pts = self._tempo_in_pts
                        self._tempo_in_pts += len(chunk)
                        f.planes[0].update(chunk.astype(np.float32).tobytes())
                        t_src.push(f)
                        while True:
                            try:
                                of = t_sink.pull()
                                arr = of.to_ndarray().flatten().astype(np.float32)
                                self._tempo_fifo = np.concatenate([self._tempo_fifo, arr]) if len(self._tempo_fifo) else arr
                            except Exception:
                                break

                    if len(self._tempo_fifo) >= self.block_size:
                        mixed_pcm = self._tempo_fifo[:self.block_size]
                        self._tempo_fifo = self._tempo_fifo[self.block_size:]
                    elif len(self._tempo_fifo) > 0:
                        pad = np.zeros(self.block_size - len(self._tempo_fifo), dtype=np.float32)
                        mixed_pcm = np.concatenate([self._tempo_fifo, pad])
                        self._tempo_fifo = np.empty(0, dtype=np.float32)
                    else:
                        mixed_pcm = np.zeros(self.block_size, dtype=np.float32)
                except Exception:
                    mixed_pcm = self._read_mixed_block(self._media_read_sample)
                    self._media_read_sample += len(mixed_pcm)
            else:
                mixed_pcm = self._read_mixed_block(self._media_read_sample)
                self._media_read_sample += len(mixed_pcm)

            # 3. Resample to output sink sample rate preserving filter continuity across blocks
            if self._sink_sr == self.internal_sr:
                out_mono = mixed_pcm
            elif av is not None:
                if getattr(self, "_sink_resampler", None) is None:
                    self._sink_resampler = av.AudioResampler(format="flt", layout="mono", rate=self._sink_sr)
                f = av.AudioFrame(format="flt", layout="mono", samples=len(mixed_pcm))
                f.sample_rate = self.internal_sr
                f.planes[0].update(mixed_pcm.astype(np.float32).tobytes())
                out_frames = self._sink_resampler.resample(f)
                if out_frames:
                    out_mono = np.concatenate([of.to_ndarray().flatten() for of in out_frames])
                else:
                    out_mono = np.empty(0, dtype=np.float32)
            else:
                out_mono = scipy.signal.resample_poly(mixed_pcm, up, down).astype(np.float32)

            if len(out_mono) == 0:
                break

            # 4. Expand channels (mono -> stereo / multi-channel surround if needed)
            if self._sink_channels > 1:
                out_audio = np.tile(out_mono[:, None], (1, self._sink_channels))
            else:
                out_audio = out_mono

            # 5. Convert to target format bytes
            if self._sink_is_float:
                data_bytes = out_audio.astype(np.float32).tobytes()
            else:
                data_bytes = (out_audio * 32767.0).clip(-32768.0, 32767.0).astype(np.int16).tobytes()

            # 6. Push to sink and advance clock only by actual written bytes
            bytes_written = self._io_device.write(data_bytes)
            if bytes_written > 0:
                frames_written = bytes_written // bytes_per_frame
                ms_written = frames_written * 1000.0 / self._sink_sr
                self._timeline_pos_exact_ms += ms_written * self._playback_rate
                if bytes_written < len(data_bytes):
                    self._pending_write_bytes = data_bytes[bytes_written:]
                    break
            else:
                self._pending_write_bytes = data_bytes
                break

            blocks_written += 1

        self.positionChanged.emit(self._timeline_pos_ms)

    @Slot()
    def close(self) -> None:
        """Release audio sink, timer, and readers."""
        self._is_playing = False
        if self._timer:
            self._timer.stop()
            self._timer = None

        if self._sink:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink = None
            self._io_device = None

        self._tempo_graph = None
        self._sink_resampler = None

        for reader in self._readers.values():
            try:
                reader.close()
            except Exception:
                pass
        self._readers.clear()
        self._pcm_cache.clear()


class PreviewAudioEngine(QObject):
    """Thread-safe public facade for real-time preview audio mixing and playback."""

    timelinePositionChanged = Signal(int)
    stateChanged = Signal(int)
    error = Signal(str)
    sinkReady = Signal(bool)

    # Internal signals for safe cross-thread queued dispatching to worker
    _sig_set_tracks = Signal(list, list)
    _sig_set_warps = Signal(list)
    _sig_set_track_gain = Signal(str, float, bool)
    _sig_seek = Signal(int)
    _sig_play = Signal()
    _sig_pause = Signal()
    _sig_stop = Signal()
    _sig_set_rate = Signal(float)
    _sig_close = Signal()

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._worker_thread = QThread()
        self._worker = _PreviewAudioWorker()
        self._worker.moveToThread(self._worker_thread)

        self._worker.positionChanged.connect(self.timelinePositionChanged)
        self._worker.stateChanged.connect(self.stateChanged)
        self._worker.errorOccurred.connect(self.error)
        self._is_ready: bool = False
        self._worker.sinkReady.connect(self._on_sink_ready)

        # Connect internal control signals
        self._sig_set_tracks.connect(self._worker.set_tracks)
        self._sig_set_warps.connect(self._worker.set_warps)
        self._sig_set_track_gain.connect(self._worker.set_track_gain)
        self._sig_seek.connect(self._worker.seek)
        self._sig_play.connect(self._worker.play)
        self._sig_pause.connect(self._worker.pause)
        self._sig_stop.connect(self._worker.stop)
        self._sig_set_rate.connect(self._worker.set_rate)
        self._sig_close.connect(self._worker.close)

        self._worker_thread.started.connect(self._worker.init_sink)
        self._worker_thread.start()

        self._cached_position_ms: int = 0
        self.timelinePositionChanged.connect(self._update_cached_pos)

    def _on_sink_ready(self, ready: bool) -> None:
        self._is_ready = bool(ready)
        self.sinkReady.emit(self._is_ready)

    def is_ready(self) -> bool:
        return self._is_ready

    def is_playing(self) -> bool:
        return bool(getattr(self._worker, "_is_playing", False))

    def _update_cached_pos(self, pos_ms: int) -> None:
        self._cached_position_ms = pos_ms

    def set_tracks(self, tracks: List[Dict[str, Any]], warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update active audio tracks snapshot."""
        self._sig_set_tracks.emit(tracks, warps or [])

    def set_warps(self, warps: Optional[List[Dict[str, Any]]] = None) -> None:
        """Update timeline warp markers."""
        self._sig_set_warps.emit(list(warps or []))

    def set_track_gain(self, track_id: str, gain: float, muted: bool = False) -> None:
        """Update track volume without regenerating media files."""
        self._sig_set_track_gain.emit(str(track_id), float(gain), bool(muted))

    def seek(self, timeline_ms: int) -> None:
        """Seek audio playback to timeline millisecond."""
        self._cached_position_ms = int(timeline_ms)
        self._sig_seek.emit(int(timeline_ms))

    def play(self) -> None:
        """Start audio playback."""
        self._sig_play.emit()

    def pause(self) -> None:
        """Pause audio playback."""
        self._sig_pause.emit()

    def stop(self) -> None:
        """Stop audio playback."""
        self._cached_position_ms = 0
        self._sig_stop.emit()

    def set_rate(self, rate: float) -> None:
        """Set playback rate."""
        self._sig_set_rate.emit(float(rate))

    def timeline_position_ms(self) -> int:
        """Return current timeline position in milliseconds."""
        return self._cached_position_ms

    def close(self) -> None:
        """Clean up thread, worker, and audio sinks."""
        self._sig_close.emit()
        if self._worker_thread.isRunning():
            self._worker_thread.quit()
            self._worker_thread.wait(2000)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

