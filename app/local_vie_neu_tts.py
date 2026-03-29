from __future__ import annotations

import json
import os
import re
import subprocess
import threading


DEFAULT_BACKBONE_REPO = "pnnbao-ump/VieNeu-TTS-0.3B-q4-gguf"
DEFAULT_CODEC_REPO = "neuphonic/distill-neucodec"
_ENGINE_LOCK = threading.Lock()
_ENGINE_INSTANCE = None
DEFAULT_CLONE_REFERENCE_TEXT = (
    "Xin chào, hôm nay là một ngày thật đẹp. Tôi đang thử ghi âm giọng nói để tạo ra bản sao giọng nói của mình. Hy vọng kết quả sẽ thật tự nhiên và rõ ràng."
)


def _ffmpeg_path() -> str:
    return os.path.join(os.getcwd(), "bin", "ffmpeg", "ffmpeg.exe")


def _sanitize_filename(name: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", str(name or "tts"), flags=re.UNICODE).strip()[:120] or "tts"


def _normalize_infer_text(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").replace("\r", " ").replace("\n", " ")).strip()
    if normalized and normalized[-1] not in ".!?…":
        normalized += "."
    return normalized


def _workspace_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _catalog_path() -> str:
    return os.path.join(_workspace_root(), "app", "voice_preview_catalog.json")


def _load_clone_catalog() -> dict[str, dict]:
    path = _catalog_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            payload = json.load(handle)
    except Exception:
        return {}

    entries = {}
    for entry in payload.get("voices", []) or []:
        provider = str(entry.get("provider", "")).strip().lower()
        if provider not in ("vieneuclone", "eleven"):
            continue
        normalized_entry = dict(entry)
        if provider == "eleven":
            normalized_entry["provider"] = "vieneuclone"
            normalized_entry["provider_voice"] = str(normalized_entry.get("id", "")).strip()
            normalized_entry["reference_audio_path"] = (
                str(normalized_entry.get("reference_audio_path", "")).strip()
                or str(normalized_entry.get("preview_audio_path", "")).strip()
            )
            normalized_entry["reference_text"] = (
                str(normalized_entry.get("reference_text", "")).strip()
                or DEFAULT_CLONE_REFERENCE_TEXT
            )
        entry_id = str(normalized_entry.get("id", "")).strip()
        provider_voice = str(normalized_entry.get("provider_voice", "")).strip()
        if entry_id:
            entries[entry_id] = dict(normalized_entry)
        if provider_voice:
            entries[provider_voice] = dict(normalized_entry)
    return entries


def _resolve_clone_reference(clone_id: str) -> tuple[str, str]:
    entry = _load_clone_catalog().get(str(clone_id or "").strip())
    if not entry:
        raise RuntimeError(f"VieNeu clone voice '{clone_id}' was not found in voice_preview_catalog.json.")

    audio_path = (
        str(entry.get("reference_audio_path", "")).strip()
        or str(entry.get("preview_audio_path", "")).strip()
    )
    if not audio_path:
        raise RuntimeError(f"VieNeu clone voice '{clone_id}' is missing reference_audio_path.")
    if not os.path.isabs(audio_path):
        audio_path = os.path.join(_workspace_root(), audio_path.replace("/", os.sep))
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"VieNeu clone reference audio not found: {audio_path}")

    reference_text = str(entry.get("reference_text", "")).strip() or DEFAULT_CLONE_REFERENCE_TEXT
    return audio_path, reference_text


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


def _save_and_convert_audio(engine, audio, wav_path: str, tmp_dir: str, suffix: str) -> str:
    ffmpeg = _ffmpeg_path()
    if not os.path.exists(ffmpeg):
        raise FileNotFoundError(f"FFmpeg not found at {ffmpeg}")

    base = _sanitize_filename(os.path.splitext(os.path.basename(wav_path))[0] or "vieneu_tts")
    intermediate_wav = os.path.join(tmp_dir, f"{base}_{suffix}.wav")
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


def _infer_with_retry(engine, *, text: str, **kwargs):
    attempts = []
    last_exc = None
    raw_text = str(text or "").strip()
    normalized_text = _normalize_infer_text(raw_text)
    for candidate in [raw_text, normalized_text]:
        candidate = str(candidate or "").strip()
        if not candidate or candidate in attempts:
            continue
        attempts.append(candidate)
        try:
            return engine.infer(text=candidate, **kwargs)
        except Exception as exc:
            error_text = str(exc or "")
            if "No valid speech tokens found in the output." not in error_text:
                raise
            last_exc = exc
    snippet = (normalized_text or raw_text)[:140]
    raise RuntimeError(
        "This clone voice could not read one subtitle line.\n"
        f"Problematic text: {snippet!r}\n\n"
        "Please try one of these:\n"
        "- Edit this subtitle line to be clearer or more natural.\n"
        "- Split the line into a simpler sentence.\n"
        "- Choose a different clone voice."
    ) from last_exc


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

    engine = _get_engine()
    voice = engine.get_preset_voice(voice_id or None)
    audio = _infer_with_retry(engine, text=text, voice=voice)
    return _save_and_convert_audio(engine, audio, wav_path, tmp_dir, "vieneu_24k")


def synthesize_clone_to_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    clone_id: str,
    tmp_dir: str | None = None,
) -> str:
    if tmp_dir is None:
        tmp_dir = os.path.join(os.getcwd(), "temp")
    os.makedirs(os.path.dirname(wav_path) or ".", exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)

    ref_audio, ref_text = _resolve_clone_reference(clone_id)
    engine = _get_engine()
    audio = _infer_with_retry(engine, text=text, ref_audio=ref_audio, ref_text=ref_text)
    return _save_and_convert_audio(engine, audio, wav_path, tmp_dir, "vieneu_clone_24k")
