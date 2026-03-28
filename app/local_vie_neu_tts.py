from __future__ import annotations

import os
import re
import subprocess
import threading


DEFAULT_BACKBONE_REPO = "pnnbao-ump/VieNeu-TTS-0.3B-q4-gguf"
DEFAULT_CODEC_REPO = "neuphonic/distill-neucodec"
_ENGINE_LOCK = threading.Lock()
_ENGINE_INSTANCE = None


def _ffmpeg_path() -> str:
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", str(name or "tts"), flags=re.UNICODE).strip()[:120] or "tts"


def _build_engine():
    try:
        from vieneu import Vieneu
        from vieneu.base import BaseVieneuTTS
        from vieneu.standard import VieNeuTTS
    except ImportError as exc:
        raise ImportError(
            "Missing dependency 'vieneu'. Please install it before using VieNeu voices."
        ) from exc

    backbone_repo = str(os.getenv("VIENEU_TTS_BACKBONE_REPO", DEFAULT_BACKBONE_REPO)).strip() or DEFAULT_BACKBONE_REPO
    codec_repo = str(os.getenv("VIENEU_TTS_CODEC_REPO", DEFAULT_CODEC_REPO)).strip() or DEFAULT_CODEC_REPO
    backbone_device = str(os.getenv("VIENEU_TTS_BACKBONE_DEVICE", "cpu")).strip() or "cpu"
    codec_device = str(os.getenv("VIENEU_TTS_CODEC_DEVICE", "cpu")).strip() or "cpu"

    kwargs = {
        "backbone_repo": backbone_repo,
        "codec_repo": codec_repo,
        "backbone_device": backbone_device,
        "codec_device": codec_device,
    }
    hf_token = str(os.getenv("HF_TOKEN", "")).strip()
    if hf_token:
        kwargs["hf_token"] = hf_token

    max_context = int(str(os.getenv("VIENEU_TTS_MAX_CONTEXT", "4096")).strip() or "4096")

    class CapCapVieNeuTTS(VieNeuTTS):
        def __init__(
            self,
            backbone_repo: str,
            backbone_device: str,
            codec_repo: str,
            codec_device: str,
            hf_token: str | None = None,
            max_context: int = 4096,
        ):
            BaseVieneuTTS.__init__(self)
            self.max_context = max(2048, int(max_context or 4096))

            self.streaming_overlap_frames = 1
            self.streaming_frames_per_chunk = 25
            self.streaming_lookforward = 10
            self.streaming_lookback = 100
            self.streaming_stride_samples = self.streaming_frames_per_chunk * self.hop_length

            self._is_quantized_model = False
            self._is_onnx_codec = False
            self.tokenizer = None
            self.backbone = None
            self.codec = None

            if backbone_repo:
                self._load_backbone(backbone_repo, backbone_device, hf_token)
            self._load_codec(codec_repo, codec_device)
            self._load_voices(backbone_repo, hf_token)
            self._warmup_model()

    if str(os.getenv("VIENEU_TTS_FORCE_FACTORY", "")).strip() == "1":
        return Vieneu(**kwargs)

    return CapCapVieNeuTTS(max_context=max_context, **kwargs)


def _get_engine():
    global _ENGINE_INSTANCE
    if _ENGINE_INSTANCE is not None:
        return _ENGINE_INSTANCE

    with _ENGINE_LOCK:
        if _ENGINE_INSTANCE is None:
            _ENGINE_INSTANCE = _build_engine()
        return _ENGINE_INSTANCE


def synthesize_text_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice_id: str = "",
    tmp_dir: str | None = None,
) -> str:
    if tmp_dir is None:
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    engine = _get_engine()
    voice = engine.get_preset_voice(voice_id or None)
    audio = engine.infer(text=text, voice=voice)

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "vieneu_tts")
    intermediate_wav = os.path.join(tmp_dir, f"{base}_vieneu_24k.wav")
    engine.save(audio, intermediate_wav)

    cmd = [
        ffmpeg,
        "-y",
        "-i",
        intermediate_wav,
        "-ar",
        "16000",
        "-ac",
        "1",
        wav_path,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg conversion failed:\n{proc.stderr or proc.stdout}")
    return wav_path
