import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
from pathlib import Path

from runtime_paths import app_path, asset_path, bin_path, models_path, temp_path, subprocess_text_kwargs

_VIENEU_MODEL = None
_VIENEU_MODEL_LOCK = threading.Lock()

VIENEU_PRESET_VOICE_META = {
    "Minh Đức": {
        "name": "Minh Đức",
        "gender": "male",
        "desc": "Nam · Miền Bắc · Phong cách tin tức, phóng sự",
    },
    "Phạm Tuyên": {
        "name": "Phạm Tuyên",
        "gender": "male",
        "desc": "Nam · Miền Bắc · Trầm ấm, đối thoại tự nhiên",
    },
    "Thái Sơn": {
        "name": "Thái Sơn",
        "gender": "male",
        "desc": "Nam · Miền Nam · Phong cách kể chuyện, podcast",
    },
    "Xuân Vĩnh": {
        "name": "Xuân Vĩnh",
        "gender": "male",
        "desc": "Nam · Miền Nam · Giọng ấm, dẫn chuyện tự nhiên",
    },
    "Thanh Bình": {
        "name": "Thanh Bình",
        "gender": "male",
        "desc": "Nam · Miền Bắc · Truyền cảm, kể chuyện, sách nói",
    },
    "Trúc Ly": {
        "name": "Trúc Ly",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Giọng trẻ, nhẹ nhàng, tự nhiên",
    },
    "Ngọc Linh": {
        "name": "Ngọc Linh",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Phong cách kể chuyện, diễn cảm",
    },
    "Đoan Trang": {
        "name": "Đoan Trang",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Trong trẻo, đối thoại tự nhiên",
    },
    "Mai Anh": {
        "name": "Mai Anh",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Dõng dạc, phong cách bản tin",
    },
    "Thục Đoan": {
        "name": "Thục Đoan",
        "gender": "female",
        "desc": "Nữ · Miền Nam · Ngọt ngào, kể chuyện, radio",
    },
    "Minh Triết": {
        "name": "Minh Triết",
        "gender": "male",
        "desc": "Nam · Miền Nam · Chững chạc, đọc tin tức, thời sự",
    },
    "Thùy Dung": {
        "name": "Thùy Dung",
        "gender": "female",
        "desc": "Nữ · Miền Nam · Lưu loát, đọc tin tức, phóng sự",
    },
    "Quang Sơn": {
        "name": "Quang Sơn",
        "gender": "male",
        "desc": "Nam · Miền Trung · Giọng miền Trung ấm áp, tự nhiên",
    },
    "Ngọc Trân": {
        "name": "Ngọc Trân",
        "gender": "female",
        "desc": "Nữ · Miền Trung · Dịu dàng, giọng Trung truyền cảm",
    },
    "Mỹ Duyên": {
        "name": "Mỹ Duyên",
        "gender": "female",
        "desc": "Nữ · Miền Nam · Đọc truyện, sâu lắng, audiobook",
    },
    "Quỳnh Anh": {
        "name": "Quỳnh Anh",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Đọc truyện, diễn cảm, tâm sự",
    },
    "Đức Trí": {
        "name": "Đức Trí",
        "gender": "male",
        "desc": "Nam · Miền Nam · Trầm hùng, đọc truyện, thuyết minh",
    },
    "Kim Thanh": {
        "name": "Kim Thanh",
        "gender": "female",
        "desc": "Nữ · Miền Nam · Diễn cảm, đọc truyện, tiểu thuyết",
    },
    "Ngọc Huyền": {
        "name": "Ngọc Huyền",
        "gender": "female",
        "desc": "Nữ · Miền Bắc · Giọng đọc tự nhiên, đời thường",
    },
    "Adam": {
        "name": "Adam",
        "gender": "male",
        "desc": "Nam · Miền Nam · Giọng trẻ, năng động, tự nhiên",
    },
}


def setup_vieneu_hf_env():
    """Ensure HF_HOME is set to existing local model directory if available."""
    if "HF_HOME" in os.environ and os.path.isdir(os.environ["HF_HOME"]):
        return
    local_vieneu = models_path("vieneu")
    local_hf = models_path("huggingface")
    shared_hf = r"D:\CodingTime\TTS_Resource\huggingface"
    local_vieneu_hub = os.path.join(local_vieneu, "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
    local_hf_hub = os.path.join(local_hf, "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
    shared_hub = os.path.join(shared_hf, "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
    if os.path.isdir(local_vieneu_hub) or os.path.isdir(os.path.join(local_vieneu, "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")):
        os.environ["HF_HOME"] = local_vieneu
    elif os.path.isdir(local_hf_hub):
        os.environ["HF_HOME"] = local_hf
    elif os.path.isdir(shared_hub):
        os.environ["HF_HOME"] = shared_hf
    elif os.path.isdir(local_vieneu):
        os.environ["HF_HOME"] = local_vieneu
    else:
        os.environ["HF_HOME"] = local_vieneu


def get_bundled_voices_dir() -> str:
    """Return the bundled directory where preset reference voices reside."""
    return asset_path("voices")


def get_vieneu_voices_dir() -> str:
    """Return the directory where custom user-created VieNeu cloned voices reside."""
    d = models_path("vieneu", "voices")
    os.makedirs(d, exist_ok=True)
    return d


def get_cached_vieneu_model(on_progress: callable = None):
    """Load or return cached Vieneu engine instance."""
    global _VIENEU_MODEL
    with _VIENEU_MODEL_LOCK:
        if _VIENEU_MODEL is None:
            if on_progress:
                on_progress("Loading VieNeu-TTS v3 Turbo (ONNX)...")
            setup_vieneu_hf_env()
            from vieneu import Vieneu
            _VIENEU_MODEL = Vieneu(mode="v3turbo", backend="onnx")
            if on_progress:
                on_progress("VieNeu-TTS loaded successfully.")
        return _VIENEU_MODEL


def list_vieneu_preset_voices() -> list[dict]:
    """Return all 20 default VieNeu preset voices formatted for voice catalog."""
    result = []
    for vid, meta in VIENEU_PRESET_VOICE_META.items():
        result.append({
            "id": f"vieneu:{vid}",
            "name": f"{meta['name']} (VieNeu)",
            "provider": "vieneu",
            "provider_voice": vid,
            "language": "vi",
            "gender": meta.get("gender", "female"),
            "tier": "free",
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": "",
            "preview_audio_path": "",
            "enabled": True,
            "tags": ["local", "vieneu", "preset"],
            "description": meta.get("desc", ""),
            "is_clone": False,
        })
    return result


def list_vieneu_cloned_voices() -> list[dict]:
    """Return all cloned and pre-downloaded reference voices from voices.json."""
    search_dirs = [get_vieneu_voices_dir(), get_bundled_voices_dir()]
    result = []
    seen_stems = set()

    for vdir in search_dirs:
        if not vdir or not os.path.isdir(vdir):
            continue
        meta_file = os.path.join(vdir, "voices.json")
        if not os.path.exists(meta_file):
            continue

        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception as exc:
            print(f"[VieNeu] Failed to read voices.json in {vdir}: {exc}")
            continue

        for e in entries:
            if not isinstance(e, dict):
                continue
            audio_rel = str(e.get("audio_path", "")).strip()
            stem = Path(audio_rel).stem
            if not stem or stem in seen_stems:
                continue
            audio_full = os.path.join(vdir, audio_rel)
            if not os.path.exists(audio_full):
                found = False
                for ext in (".mp3", ".wav", ".m4a", ".flac"):
                    alt = os.path.join(vdir, f"{stem}{ext}")
                    if os.path.exists(alt):
                        audio_full = alt
                        found = True
                        break
                if not found:
                    continue

            seen_stems.add(stem)
            name = str(e.get("name", stem.replace("_", " ").title())).strip() or stem
            gender = str(e.get("gender", "male")).strip().lower()
            desc = str(e.get("description", "")).strip()
            text_ref = str(e.get("text_ref", "")).strip()

            result.append({
                "id": f"vieneu_clone:{stem}",
                "name": f"{name} (Clone)",
                "provider": "vieneu",
                "provider_voice": stem,
                "language": "vi",
                "gender": gender,
                "tier": "free",
                "preview_video_url": "",
                "preview_video_path": "",
                "preview_audio_url": "",
                "preview_audio_path": "",
                "enabled": True,
                "tags": ["local", "vieneu", "clone"],
                "description": desc or "Voice cloned from reference audio sample.",
                "is_clone": True,
                "ref_audio": audio_full,
                "ref_text": text_ref,
            })
    return result


def list_all_vieneu_voices() -> list[dict]:
    """Return all VieNeu voices (cloned/reference voices + presets)."""
    return list_vieneu_cloned_voices() + list_vieneu_preset_voices()


def _slugify_name(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    result = "".join(c for c in nfkd if not unicodedata.combining(c))
    result = result.replace("đ", "d").replace("Đ", "D")
    result = re.sub(r"[^a-zA-Z0-9]+", "_", result).strip("_").lower()
    return result or "clone_voice"


def save_cloned_voice(
    *,
    name: str,
    audio_path: str,
    ref_text: str,
    gender: str = "male",
    description: str = "",
) -> dict:
    """Save a new reference audio and update voices.json."""
    if not name.strip():
        raise ValueError("Voice name cannot be empty.")
    if not audio_path or not os.path.exists(audio_path):
        raise FileNotFoundError(f"Reference audio file not found: {audio_path}")
    if not ref_text.strip():
        raise ValueError("Reference transcript cannot be empty.")

    vdir = get_vieneu_voices_dir()
    slug = _slugify_name(name)
    ext = os.path.splitext(audio_path)[1].lower() or ".wav"
    target_filename = f"{slug}{ext}"
    target_path = os.path.join(vdir, target_filename)

    counter = 1
    while os.path.exists(target_path) and os.path.abspath(audio_path) != os.path.abspath(target_path):
        target_filename = f"{slug}_{counter}{ext}"
        target_path = os.path.join(vdir, target_filename)
        counter += 1

    if os.path.abspath(audio_path) != os.path.abspath(target_path):
        shutil.copy2(audio_path, target_path)

    meta_file = os.path.join(vdir, "voices.json")
    entries = []
    if os.path.exists(meta_file):
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except Exception:
            entries = []

    voice_stem = Path(target_filename).stem
    updated = False
    for e in entries:
        if Path(e.get("audio_path", "")).stem == voice_stem:
            e["name"] = name.strip()
            e["gender"] = gender.strip().lower()
            e["audio_path"] = target_filename
            e["description"] = description.strip()
            e["text_ref"] = ref_text.strip()
            e["clone"] = True
            updated = True
            break

    if not updated:
        entries.append({
            "name": name.strip(),
            "gender": gender.strip().lower(),
            "audio_path": target_filename,
            "description": description.strip(),
            "text_ref": ref_text.strip(),
            "clone": True,
        })

    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)

    return {
        "id": f"vieneu_clone:{voice_stem}",
        "name": f"{name.strip()} (Clone)",
        "ref_audio": target_path,
        "ref_text": ref_text.strip(),
        "gender": gender,
    }


def _ffmpeg_path() -> str:
    for candidate in [bin_path("ffmpeg", "ffmpeg.exe"), bin_path("ffmpeg.exe")]:
        if os.path.isfile(candidate):
            return candidate
    return "ffmpeg"


def vieneu_synthesize_wav_16k_mono(
    *,
    text: str,
    wav_path: str,
    voice_id: str,
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
) -> str:
    """Synthesize text using VieNeu-TTS and write 16kHz mono WAV."""
    if not text or not text.strip():
        raise ValueError("No text provided for VieNeu synthesis.")

    if tmp_dir is None:
        tmp_dir = temp_path()
    os.makedirs(tmp_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)

    model = get_cached_vieneu_model(on_progress=on_progress)

    clean_voice = str(voice_id or "").strip()
    is_clone = clean_voice.startswith("vieneu_clone:")
    raw_stem = clean_voice.replace("vieneu_clone:", "").replace("vieneu:", "").strip()

    ref_audio = None
    ref_text = None

    if is_clone or raw_stem not in VIENEU_PRESET_VOICE_META:
        search_dirs = [get_vieneu_voices_dir(), get_bundled_voices_dir()]
        for vdir in search_dirs:
            if not vdir or not os.path.isdir(vdir):
                continue
            meta_file = os.path.join(vdir, "voices.json")
            if os.path.exists(meta_file):
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        entries = json.load(f)
                    for e in entries:
                        if Path(e.get("audio_path", "")).stem == raw_stem:
                            ref_text = e.get("text_ref", "")
                            candidate_audio = os.path.join(vdir, e.get("audio_path", ""))
                            if os.path.exists(candidate_audio):
                                ref_audio = candidate_audio
                            break
                except Exception as exc:
                    print(f"[VieNeu] Error loading clone entry for {raw_stem} in {vdir}: {exc}")

            if not ref_audio:
                for ext in (".mp3", ".wav", ".m4a", ".flac"):
                    candidate = os.path.join(vdir, f"{raw_stem}{ext}")
                    if os.path.exists(candidate):
                        ref_audio = candidate
                        break

            if ref_audio:
                break

    if ref_audio and os.path.exists(ref_audio):
        if on_progress:
            on_progress(f"Synthesizing with clone voice '{raw_stem}'...")
        audio_data = model.infer(text.strip(), ref_audio=ref_audio, ref_text=ref_text or "")
    else:
        preset_name = raw_stem if raw_stem in VIENEU_PRESET_VOICE_META else "Ngọc Huyền"
        if on_progress:
            on_progress(f"Synthesizing with preset voice '{preset_name}'...")
        audio_data = model.infer(text.strip(), voice=preset_name)

    import soundfile as sf
    from uuid import uuid4
    temp_48k_path = os.path.join(tmp_dir, f"vieneu_raw_{uuid4().hex[:8]}.wav")
    try:
        sf.write(temp_48k_path, audio_data, 48000, subtype="PCM_16")

        ffmpeg = _ffmpeg_path()
        filter_args = []
        speed_float = float(speed or 1.0)
        if abs(speed_float - 1.0) >= 0.02 and 0.5 <= speed_float <= 2.0:
            filter_args = ["-filter:a", f"atempo={speed_float}"]

        cmd = [
            ffmpeg, "-y", "-i", temp_48k_path,
            *filter_args,
            "-ar", "16000",
            "-ac", "1",
            wav_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg conversion to 16kHz failed: {proc.stderr or proc.stdout}")
    finally:
        if os.path.exists(temp_48k_path):
            try:
                os.remove(temp_48k_path)
            except Exception:
                pass

    return wav_path
