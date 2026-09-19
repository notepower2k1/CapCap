import json
import os
import re
import shutil
import subprocess
import sys
import threading
import unicodedata
from pathlib import Path

from runtime_paths import (
    app_path,
    asset_path,
    bin_path,
    bundle_root,
    models_path,
    temp_path,
    workspace_root,
    subprocess_text_kwargs,
)


def _patch_sea_g2p_db_path() -> bool:
    """Patch sea_g2p.g2p.G2P to locate sea_g2p.bin in frozen PyInstaller environments.

    In frozen environments (onedir), pure Python files reside in base_library.zip,
    making os.path.dirname(__file__) point inside the zip archive where native C/Rust
    file opening fails with OSError: The system cannot find the file specified.
    """
    try:
        import sea_g2p.g2p
        from sea_g2p.sea_g2p_rs import G2P as _RustG2P

        if getattr(sea_g2p.g2p.G2P, "_capcap_patched", False):
            return True

        def _resolve_sea_g2p_bin(passed_db=None):
            candidates = []
            if passed_db:
                candidates.append(passed_db)

            # PyInstaller bundle root (_internal or _MEIPASS)
            candidates.append(os.path.join(bundle_root(), "sea_g2p", "sea_g2p.bin"))
            candidates.append(os.path.join(bundle_root(), "_internal", "sea_g2p", "sea_g2p.bin"))
            candidates.append(os.path.join(workspace_root(), "sea_g2p", "sea_g2p.bin"))
            candidates.append(os.path.join(workspace_root(), "_internal", "sea_g2p", "sea_g2p.bin"))

            meipass = getattr(sys, "_MEIPASS", "")
            if meipass:
                candidates.append(os.path.join(str(meipass), "sea_g2p", "sea_g2p.bin"))
                candidates.append(os.path.join(str(meipass), "_internal", "sea_g2p", "sea_g2p.bin"))

            # Development / virtualenv package location
            try:
                import sea_g2p
                if hasattr(sea_g2p, "__file__") and sea_g2p.__file__:
                    candidates.append(os.path.join(os.path.dirname(sea_g2p.__file__), "sea_g2p.bin"))
            except Exception:
                pass

            try:
                if hasattr(sea_g2p.g2p, "__file__") and sea_g2p.g2p.__file__:
                    candidates.append(os.path.join(os.path.dirname(sea_g2p.g2p.__file__), "sea_g2p.bin"))
            except Exception:
                pass

            for candidate in candidates:
                if candidate and os.path.isfile(candidate):
                    return candidate
            return None

        def patched_init(self, lang: str = "vi", db_path: str = None):
            if lang not in sea_g2p.g2p.SUPPORTED_LANGS:
                raise ValueError(f"lang must be one of {sea_g2p.g2p.SUPPORTED_LANGS}, got {lang!r}")
            self.lang = lang
            resolved = _resolve_sea_g2p_bin(db_path)
            if not resolved:
                resolved = os.path.join(os.path.dirname(sea_g2p.g2p.__file__), "sea_g2p.bin")
            self._rust_engine = _RustG2P(resolved)
            sea_g2p.g2p.logger.debug(f"Initialized Rust G2P engine with {resolved}")

        sea_g2p.g2p.G2P.__init__ = patched_init
        sea_g2p.g2p.G2P._capcap_patched = True
        return True
    except Exception:
        return False


_patch_sea_g2p_db_path()

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
    local_vieneu_hub = os.path.join(local_vieneu, "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
    local_hf_hub = os.path.join(local_hf, "hub", "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")
    if os.path.isdir(local_vieneu_hub) or os.path.isdir(os.path.join(local_vieneu, "models--pnnbao-ump--VieNeu-TTS-v3-Turbo")):
        os.environ["HF_HOME"] = local_vieneu
    elif os.path.isdir(local_hf_hub):
        os.environ["HF_HOME"] = local_hf
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
            _patch_sea_g2p_db_path()
            setup_vieneu_hf_env()
            from vieneu import Vieneu
            precision = os.getenv("CAPCAP_VIENEU_PRECISION", "int8").strip().lower()
            if precision not in ("fp32", "int8"):
                precision = "int8"
            cpu_cnt = os.cpu_count() or 4
            default_threads = max(1, min(cpu_cnt - 2, 10)) if cpu_cnt >= 8 else min(cpu_cnt, 4)
            threads_env = os.getenv("CAPCAP_VIENEU_THREADS")
            try:
                threads = int(threads_env) if threads_env else default_threads
            except (ValueError, TypeError):
                threads = default_threads
            _VIENEU_MODEL = Vieneu(mode="v3turbo", backend="onnx", precision=precision, threads=threads)
            if on_progress:
                on_progress("VieNeu-TTS loaded successfully.")
        return _VIENEU_MODEL


def unload_vieneu_model():
    """Unload cached VieNeu model and free its memory and ONNX sessions."""
    global _VIENEU_MODEL, _CLONE_VOICE_CACHE_KEYS
    with _VIENEU_MODEL_LOCK:
        if _VIENEU_MODEL is not None:
            try:
                engine = getattr(_VIENEU_MODEL, "engine", None)
                if engine is not None:
                    for attr in [
                        "sess_pre", "sess_dec", "sess_ac", "sess_codec_dec",
                        "_sess_codec_enc", "sess_codec_step", "speaker_encoder", "denoiser"
                    ]:
                        setattr(engine, attr, None)
                    _VIENEU_MODEL.engine = None
                if hasattr(_VIENEU_MODEL, "_preset_voices"):
                    _VIENEU_MODEL._preset_voices.clear()
            except Exception as exc:
                print(f"[VieNeu] Error during model teardown: {exc}")
            _VIENEU_MODEL = None
            _CLONE_VOICE_CACHE_KEYS.clear()
            import gc
            gc.collect()
            print("[VieNeu] Model unloaded and RAM released", flush=True)


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


_CLONE_VOICE_REGISTER_LOCK = threading.Lock()
_CLONE_VOICE_CACHE_KEYS = {}


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

    _patch_sea_g2p_db_path()
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
        clone_voice_key = f"clone_{raw_stem}"
        file_sig = f"{os.path.getmtime(ref_audio)}_{os.path.getsize(ref_audio)}"
        with _CLONE_VOICE_REGISTER_LOCK:
            needs_register = (
                not hasattr(model, "_preset_voices")
                or clone_voice_key not in model._preset_voices
                or _CLONE_VOICE_CACHE_KEYS.get(clone_voice_key) != file_sig
            )
            if needs_register and hasattr(model, "add_voice"):
                try:
                    model.add_voice(clone_voice_key, ref_audio=ref_audio)
                    _CLONE_VOICE_CACHE_KEYS[clone_voice_key] = file_sig
                except Exception as exc:
                    print(f"[VieNeu] Warning: could not pre-register clone voice '{raw_stem}': {exc}")

        if hasattr(model, "_preset_voices") and clone_voice_key in model._preset_voices:
            audio_data = model.infer(text.strip(), voice=clone_voice_key)
        else:
            audio_data = model.infer(text.strip(), ref_audio=ref_audio, ref_text=ref_text or "")
    else:
        preset_name = raw_stem if raw_stem in VIENEU_PRESET_VOICE_META else "Ngọc Huyền"
        if on_progress:
            on_progress(f"Synthesizing with preset voice '{preset_name}'...")
        audio_data = model.infer(text.strip(), voice=preset_name)

    # Try in-process native resample + tempo + atomic write
    try:
        import numpy as np
        import soundfile as sf
        from app.media_decode import _resample_audio, _downmix_to_mono
        from app.audio_mixer import change_pcm_speed
        from uuid import uuid4

        arr = np.asarray(audio_data, dtype=np.float32)
        if np.issubdtype(audio_data.dtype, np.integer):
            arr = arr / float(np.iinfo(audio_data.dtype).max)
        mono = _downmix_to_mono(arr)
        resampled_16k = _resample_audio(mono, 48000, 16000)
        speed_float = float(speed or 1.0)
        if abs(speed_float - 1.0) >= 0.02:
            resampled_16k = change_pcm_speed(resampled_16k, sample_rate=16000, speed_ratio=speed_float)

        part_path = f"{wav_path}.{uuid4().hex[:8]}.part.wav"
        try:
            sf.write(part_path, resampled_16k, 16000, format="WAV", subtype="PCM_16")
            info = sf.info(part_path)
            if info.frames <= 0:
                raise RuntimeError(f"Generated WAV file is invalid: {part_path}")
            os.replace(part_path, wav_path)
            return wav_path
        finally:
            if os.path.exists(part_path):
                try:
                    os.remove(part_path)
                except OSError:
                    pass
    except Exception:
        pass

    import soundfile as sf
    from uuid import uuid4
    temp_48k_path = os.path.join(tmp_dir, f"vieneu_raw_{uuid4().hex[:8]}.wav")
    part_path = f"{wav_path}.{uuid4().hex[:8]}.part.wav"
    try:
        sf.write(temp_48k_path, audio_data, 48000, format="WAV", subtype="PCM_16")

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
            part_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, **subprocess_text_kwargs())
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg conversion to 16kHz failed: {proc.stderr or proc.stdout}")
        os.replace(part_path, wav_path)
    finally:
        if os.path.exists(temp_48k_path):
            try:
                os.remove(temp_48k_path)
            except Exception:
                pass
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass

    return wav_path
