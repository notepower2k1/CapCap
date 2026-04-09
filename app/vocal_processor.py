import os
import subprocess
import shutil
import sys


def _subprocess_run_kwargs() -> dict:
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        kwargs["startupinfo"] = startupinfo
    return kwargs

def _detect_demucs_device() -> str:
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"

def _write_wav_soundfile(path, audio, samplerate):
    """
    Write audio using soundfile to avoid torchaudio/torchcodec DLL issues on Windows.
    audio: torch.Tensor shaped (channels, samples) or numpy array equivalent.
    """
    try:
        import soundfile as sf
    except Exception as e:
        raise ImportError(
            "Missing dependency 'soundfile' needed for fallback audio writing.\n"
            f"Python: {sys.executable}\n"
            "Please run:\n"
            "python -m pip install soundfile\n"
            f"Original error: {e}"
        ) from e

    # Lazy import numpy/torch only when needed
    try:
        import numpy as np
    except Exception as e:
        raise ImportError(
            "Missing dependency 'numpy' needed for fallback audio writing.\n"
            f"Python: {sys.executable}\n"
            "Please run:\n"
            "python -m pip install numpy\n"
            f"Original error: {e}"
        ) from e

    arr = audio
    # torch.Tensor -> numpy
    try:
        import torch  # noqa: F401
        if hasattr(arr, "detach"):
            arr = arr.detach().cpu().float().numpy()
    except Exception:
        pass

    arr = np.asarray(arr)
    # (C, T) -> (T, C)
    if arr.ndim == 2:
        arr = arr.T
    sf.write(path, arr, samplerate)


def _separate_vocals_via_api(audio_path, output_dir):
    """
    Demucs separation via Python API + soundfile writing.
    Returns (vocal_path, music_path) or raises.
    """
    from demucs.separate import load_track
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    model = get_model("htdemucs")
    device = _detect_demucs_device()
    print(f"[Demucs] Using device: {device}")
    model.to(device)

    wav = load_track(audio_path, model.audio_channels, model.samplerate)
    # wav shape: (channels, samples)
    sources = apply_model(
        model,
        wav[None],
        device=device,
        shifts=0,
        split=True,
        overlap=0.25,
        progress=False,
    )[0]

    # sources shape: (num_sources, channels, samples)
    # Find vocals stem and build accompaniment as sum of others
    src_names = list(getattr(model, "sources", []))
    if "vocals" not in src_names:
        raise RuntimeError(f"Demucs model sources missing 'vocals': {src_names}")
    v_idx = src_names.index("vocals")
    vocals = sources[v_idx]
    other = sources.sum(0) - vocals

    base_name = os.path.splitext(os.path.basename(audio_path))[0]
    result_dir = os.path.join(output_dir, "htdemucs", base_name)
    os.makedirs(result_dir, exist_ok=True)

    vocal_out = os.path.join(result_dir, "vocals.wav")
    music_out = os.path.join(result_dir, "no_vocals.wav")
    _write_wav_soundfile(vocal_out, vocals, model.samplerate)
    _write_wav_soundfile(music_out, other, model.samplerate)
    return vocal_out, music_out


def separate_vocals(audio_path, output_dir):
    """
    Separates vocals from background music using Demucs.
    Requires: pip install demucs
    Returns: (vocal_path, music_path) or (None, None)
    """
    if not os.path.exists(audio_path):
        return None, None

    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    # We use demucs (local)
    # n htdemucs is a good default model
    # --two-stems=vocals will give us vocals and 'no_vocals' (music)
    
    # Add our local ffmpeg to PATH so demucs can find it
    ffmpeg_dir = os.path.join(os.getcwd(), 'bin', 'ffmpeg')
    if os.path.exists(ffmpeg_dir):
        os.environ["PATH"] = ffmpeg_dir + os.pathsep + os.environ.get("PATH", "")

    try:
        import demucs.separate  # noqa: F401
    except ImportError as e:
        # Fallback: if demucs isn't importable, verify it's runnable
        if shutil.which("demucs") is None:
            try:
                subprocess.run([sys.executable, "-m", "demucs", "--help"], capture_output=True, check=True, text=True, **_subprocess_run_kwargs())
            except Exception as e:
                raise ImportError("Demucs is not installed. Please run 'pip install demucs'") from e
        # If the CLI exists but import failed, propagate the real import error.
        raise ImportError(
            "Demucs import failed (it may be installed but missing a dependency).\n"
            f"Python: {sys.executable}\n"
            f"Original error: {e}"
        ) from e

    demucs_device = _detect_demucs_device()
    print(f"[Demucs] Using device: {demucs_device}")
    cmd = [
        str(sys.executable), "-m", "demucs",
        "--device", demucs_device,
        "--two-stems", "vocals",
        "-o", str(output_dir),
        str(audio_path)
    ]
    
    print(f"Running Vocal Separation: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, check=True, capture_output=True, text=True, **_subprocess_run_kwargs())
        
        # Demucs output structure varies by version:
        # - output_dir/htdemucs/<track>/vocals.wav
        # - output_dir/separated/htdemucs/<track>/vocals.wav
        # and model name may differ.
        base_name = os.path.splitext(os.path.basename(audio_path))[0]

        candidates = [
            os.path.join(output_dir, "htdemucs", base_name),
            os.path.join(output_dir, "separated", "htdemucs", base_name),
        ]

        for result_dir in candidates:
            vocal_out = os.path.join(result_dir, "vocals.wav")
            music_out = os.path.join(result_dir, "no_vocals.wav")
            if os.path.exists(vocal_out) and os.path.exists(music_out):
                return vocal_out, music_out

        # Robust scan if model name / folder differs
        vocal_found = None
        music_found = None
        for root, _, files in os.walk(output_dir):
            if base_name not in os.path.basename(root):
                continue
            if "vocals.wav" in files:
                vocal_found = os.path.join(root, "vocals.wav")
            if "no_vocals.wav" in files:
                music_found = os.path.join(root, "no_vocals.wav")
            if vocal_found and music_found:
                return vocal_found, music_found

        return None, None
    except subprocess.CalledProcessError as e:
        details = (e.stderr or e.stdout or "").strip()
        msg = f"Demucs failed (exit {e.returncode})."
        if details:
            msg += f"\n{details}"
        # Common Windows failure: torchaudio -> torchcodec DLL missing.
        if "could not load libtorchcodec" in details.lower():
            try:
                return _separate_vocals_via_api(audio_path, output_dir)
            except Exception as api_e:
                raise RuntimeError(msg + f"\n\nFallback API mode also failed:\n{api_e}") from e
        raise RuntimeError(msg) from e
    except Exception as e:
        print(f"Vocal separation error: {e}")
        raise RuntimeError(f"Vocal separation error: {e}") from e
