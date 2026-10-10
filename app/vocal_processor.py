import math
import os
import shutil
import subprocess
import tempfile
import warnings

import numpy as np
import soundfile as sf
import librosa
import onnxruntime as ort
from scipy.signal.windows import hann as periodic_hann

try:
    from runtime_paths import bin_path, subprocess_hidden_kwargs
except ImportError:
    from app.runtime_paths import bin_path, subprocess_hidden_kwargs

warnings.filterwarnings("ignore", message="NOLA condition failed")

_MODEL_PATH = None
_ONNX_SESSION = None

DIM_F = 3072
DIM_T = 256
N_FFT = DIM_F * 2
HOP = 1024
SR = 44100
L = 11
HALF_DIM_C = 2


def _model_path():
    global _MODEL_PATH
    if _MODEL_PATH is None:
        _MODEL_PATH = os.path.join(bin_path(), "UVR-MDX-NET-Inst_HQ_3.onnx")
    return _MODEL_PATH


def _ensure_cuda_runtime() -> None:
    """Ensure bundled/system CUDA and cuDNN runtime DLLs are available in PATH and os.add_dll_directory."""
    try:
        from runtime_paths import join_root, bin_path
    except ImportError:
        try:
            from app.runtime_paths import join_root, bin_path
        except ImportError:
            return
    current_path = os.environ.get("PATH", "")
    for fw_name in ("cuda12_fw", "cuda12_fw_new"):
        for candidate in (join_root("bin", fw_name), bin_path(fw_name)):
            if candidate and os.path.isdir(candidate):
                if candidate not in current_path:
                    os.environ["PATH"] = candidate + os.pathsep + os.environ.get("PATH", "")
                    current_path = os.environ["PATH"]
                if hasattr(os, "add_dll_directory"):
                    try:
                        os.add_dll_directory(candidate)
                    except Exception:
                        pass


def _get_session():
    global _ONNX_SESSION
    if _ONNX_SESSION is None:
        path = _model_path()
        if not os.path.exists(path):
            raise FileNotFoundError(f"ONNX model not found: {path}")
        _ensure_cuda_runtime()
        available = ort.get_available_providers()
        providers = [p for p in ["CUDAExecutionProvider", "CPUExecutionProvider"] if p in available]
        _ONNX_SESSION = ort.InferenceSession(
            path,
            providers=providers,
        )
    return _ONNX_SESSION


def _get_ffmpeg() -> str:
    bundled = bin_path("ffmpeg", "ffmpeg.exe")
    if os.path.isfile(bundled):
        return bundled
    alt = bin_path("ffmpeg.exe")
    if os.path.isfile(alt):
        return alt
    return shutil.which("ffmpeg") or "ffmpeg"



class _STFT:
    def __init__(self):
        self.dim_c = 4
        self.dim_f = DIM_F
        self.dim_t = DIM_T
        self.n_fft = N_FFT
        self.hop = HOP
        self.n_bins = N_FFT // 2 + 1
        self.chunk_size = HOP * (DIM_T - 1)
        window = periodic_hann(N_FFT, sym=False).astype(np.float32)
        self.window_stft = window
        self.freq_pad = np.zeros([1, self.dim_c, self.n_bins - self.dim_f, self.dim_t], dtype=np.float32)

    def stft(self, x):
        x = x.reshape(-1, self.chunk_size)
        results = []
        for i in range(x.shape[0]):
            Z = librosa.stft(
                x[i].numpy() if hasattr(x[i], 'numpy') else x[i],
                n_fft=self.n_fft,
                hop_length=self.hop,
                window=self.window_stft,
                center=True,
            )
            Z_real = np.real(Z)
            Z_imag = np.imag(Z)
            results.append(np.stack([Z_real, Z_imag], axis=-1))
        X = np.stack(results)  # (batch, n_bins, dim_t, 2)
        X = X.transpose(0, 3, 1, 2)  # (batch, 2, n_bins, dim_t)
        X = X.reshape(-1, 2, self.n_bins, self.dim_t)
        X = X.reshape(-1, self.dim_c, self.n_bins, self.dim_t)
        return X[:, :, :self.dim_f, :]

    def istft(self, x, freq_pad=None):
        if freq_pad is None:
            freq_pad = np.repeat(self.freq_pad, x.shape[0], axis=0)
        x = np.concatenate([x, freq_pad], axis=-2)
        x = x.reshape(-1, HALF_DIM_C, 2, self.n_bins, self.dim_t)
        x = x.reshape(-1, 2, self.n_bins, self.dim_t)
        x = x.transpose(0, 2, 3, 1)  # (batch, n_bins, dim_t, 2)
        results = []
        for i in range(x.shape[0]):
            Z = x[i, :, :, 0] + 1j * x[i, :, :, 1]
            wav = librosa.istft(
                Z,
                hop_length=self.hop,
                window=self.window_stft,
                center=True,
                length=self.chunk_size,
            )
            results.append(wav)
        result = np.stack(results)
        result = result.reshape(-1, HALF_DIM_C, self.chunk_size)
        return result


def separate_vocals(audio_path: str, output_dir: str, on_progress=None):
    """Separate vocals and instrumental using UVR MDX-NET model with streaming I/O.

    Streams chunk-by-chunk to disk so RAM remains strictly bounded (<200MB) even
    for long audio tracks (>4 hours).
    """
    if not os.path.exists(audio_path):
        return None, None

    os.makedirs(output_dir, exist_ok=True)
    session = _get_session()
    model = _STFT()

    orig_info = sf.info(audio_path)
    orig_sr = orig_info.samplerate
    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    result_dir = os.path.join(output_dir, "onnx_separated", base_name)
    os.makedirs(result_dir, exist_ok=True)

    vocal_out = os.path.join(result_dir, "vocals.wav")
    music_out = os.path.join(result_dir, "no_vocals.wav")

    temp_dir = tempfile.mkdtemp(prefix="uvr_sep_")
    try:
        ffmpeg_bin = _get_ffmpeg()
        temp_mix_441 = os.path.join(temp_dir, "mix_441.wav")
        # Streaming convert input audio to 44.1kHz stereo WAV via FFmpeg
        cmd = [
            ffmpeg_bin, "-y", "-i", audio_path,
            "-vn", "-ar", str(SR), "-ac", "2",
            "-c:a", "pcm_s16le",
            temp_mix_441
        ]
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **subprocess_hidden_kwargs()
        )

        mix_info = sf.info(temp_mix_441)
        samples = mix_info.frames
        margin = SR
        chunk_size = 15 * SR

        if samples < chunk_size:
            chunk_size = samples
        if margin > chunk_size:
            margin = chunk_size

        total_chunks = max(1, math.ceil(samples / chunk_size)) if chunk_size > 0 else 1

        temp_inst_441 = os.path.join(temp_dir, "inst_441.wav")
        temp_voc_441 = os.path.join(temp_dir, "voc_441.wav")

        with sf.SoundFile(temp_mix_441, "r") as in_f, \
             sf.SoundFile(temp_inst_441, "w", samplerate=SR, channels=1, subtype="FLOAT") as inst_f, \
             sf.SoundFile(temp_voc_441, "w", samplerate=SR, channels=1, subtype="FLOAT") as voc_f:

            for chunk_idx in range(total_chunks):
                skip = chunk_idx * chunk_size
                s_margin = 0 if chunk_idx == 0 else margin
                read_start = skip - s_margin
                read_end = min(skip + chunk_size + margin, samples)
                read_frames = read_end - read_start

                in_f.seek(read_start)
                cmix = in_f.read(frames=read_frames, dtype="float32", always_2d=True).T
                if cmix.shape[0] == 1:
                    cmix = np.repeat(cmix, 2, axis=0)
                elif cmix.shape[0] > 2:
                    cmix = cmix[:2]

                n_sample = cmix.shape[1]
                trim = model.n_fft // 2
                gen_size = model.chunk_size - 2 * trim
                pad = gen_size - n_sample % gen_size
                if pad == gen_size:
                    pad = 0

                mix_p = np.concatenate([
                    np.zeros((2, trim), dtype=np.float32),
                    cmix,
                    np.zeros((2, pad), dtype=np.float32),
                    np.zeros((2, trim), dtype=np.float32),
                ], axis=1)

                mix_waves = []
                i = 0
                while i < n_sample + pad:
                    waves = mix_p[:, i:i + model.chunk_size]
                    mix_waves.append(waves)
                    i += gen_size

                if not mix_waves:
                    continue

                mix_waves = np.array(mix_waves, dtype=np.float32)
                spek = model.stft(mix_waves)
                spec_pred = session.run(None, {"input": spek})[0]
                tar_waves = model.istft(spec_pred)

                tar_signal = tar_waves[:, :, trim:-trim]
                tar_signal = tar_signal.transpose(1, 0, 2).reshape(2, -1)
                tar_signal = tar_signal[:, :n_sample]

                valid_start = 0 if chunk_idx == 0 else margin
                valid_end = n_sample if chunk_idx == total_chunks - 1 else n_sample - margin
                if margin == 0:
                    valid_end = n_sample

                inst_chunk = tar_signal[0, valid_start:valid_end]
                orig_chunk = cmix[0, valid_start:valid_end]
                vocal_chunk = orig_chunk - inst_chunk

                inst_f.write(inst_chunk)
                voc_f.write(vocal_chunk)

                pct = int((chunk_idx + 1) / total_chunks * 100)
                msg = f"Separating audio: {chunk_idx + 1}/{total_chunks} chunks ({pct}%)"
                print(f"[Vocal Separation Progress] {chunk_idx + 1}/{total_chunks} chunks ({pct}%)", flush=True)
                if on_progress:
                    on_progress(pct, msg)

        # Resample back to original sample rate and save as standard 16-bit PCM WAV
        cmd_vocal = [
            ffmpeg_bin, "-y", "-i", temp_voc_441,
            "-ar", str(orig_sr), "-ac", "1",
            "-c:a", "pcm_s16le",
            vocal_out
        ]
        cmd_music = [
            ffmpeg_bin, "-y", "-i", temp_inst_441,
            "-ar", str(orig_sr), "-ac", "1",
            "-c:a", "pcm_s16le",
            music_out
        ]
        subprocess.run(
            cmd_vocal,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **subprocess_hidden_kwargs()
        )
        subprocess.run(
            cmd_music,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **subprocess_hidden_kwargs()
        )

        return vocal_out, music_out
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

