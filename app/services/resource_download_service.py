from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

<<<<<<< Updated upstream
from runtime_paths import app_path, bin_path, bundle_root, join_root, models_path, subprocess_hidden_kwargs
=======
from runtime_paths import app_path, bin_path, join_root, models_path, subprocess_hidden_kwargs

>>>>>>> Stashed changes


class ResourceDownloadService:
    WHISPER_ZIP_FILES = {
        "base": "models--Systran--faster-whisper-base.zip",
        "small": "models--Systran--faster-whisper-small.zip",
        "medium": "models--Systran--faster-whisper-medium.zip",
    }

    HF_RESOURCE_REPO = os.getenv("CAPCAP_RESOURCE_REPO", "Hacht/CapCapResource").strip() or "Hacht/CapCapResource"
    HF_RESOURCE_REVISION = os.getenv("CAPCAP_RESOURCE_REVISION", "main").strip() or "main"
    SENSEVOICE_REPO = os.getenv(
        "SENSEVOICE_MODEL_REPO",
        "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
    ).strip() or "csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root
        self.repo_id = self.HF_RESOURCE_REPO
        self.revision = self.HF_RESOURCE_REVISION

    def _catalog_path(self) -> str:
        download_catalog = app_path("voice_download_catalog.json")
        if os.path.exists(download_catalog):
            return download_catalog
        release_catalog = app_path("voice_preview_catalog.release.json")
        if os.path.exists(release_catalog):
            return release_catalog
        return app_path("voice_preview_catalog.json")

    def _read_catalog(self) -> dict:
        path = self._catalog_path()
        if not os.path.exists(path):
            return {"voices": []}
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return {"voices": []}

    def _voice_local_paths(self, voice_entry: dict) -> tuple[str, str]:
        provider_voice = str(voice_entry.get("provider_voice", "")).strip().replace("/", os.sep)
        normalized = os.path.normpath(provider_voice)
        if normalized.startswith(f"models{os.sep}"):
            # Resolve bundled resources through models_path(), which checks
            # both the writable project directory and PyInstaller's
            # _internal/models directory.  join_root() only points at the
            # executable directory in a frozen build and therefore made the
            # bundled default voice look missing.
            relative = normalized[len("models" + os.sep):]
            model_path = models_path(*Path(relative).parts)
        else:
            model_path = models_path("piper", os.path.basename(normalized))
        config_path = f"{model_path}.json"
        return (
            model_path,
            config_path,
        )

    def _voice_remote_paths(self, voice_entry: dict) -> tuple[str, str]:
        provider_voice = str(voice_entry.get("provider_voice", "")).strip().replace("\\", "/")
        model_name = os.path.basename(provider_voice)
        remote_model = provider_voice
        if remote_model.startswith("models/"):
            remote_model = remote_model[len("models/"):]
        if not remote_model:
            remote_model = f"piper/{model_name}"
        return (
            remote_model,
            f"{remote_model}.json",
        )

    def _finalize_voice_download(self, downloaded_path: str, voice_entry: dict, *, is_config: bool) -> str:
        source_path = str(downloaded_path or "").strip()
        if not source_path or not os.path.exists(source_path):
            return source_path
        model_path, config_path = self._voice_local_paths(voice_entry)
        target_path = config_path if is_config else model_path
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        normalized_source = os.path.normcase(os.path.abspath(source_path))
        normalized_target = os.path.normcase(os.path.abspath(target_path))
        if normalized_source != normalized_target:
            if os.path.exists(target_path):
                os.remove(target_path)
            shutil.move(source_path, target_path)
            self._cleanup_empty_voice_cache_dirs(os.path.dirname(source_path))
        return target_path

    def _download_hf_file(
        self,
        *,
        repo_id: str,
        revision: str,
        filename: str,
        local_dir: str,
        hf_hub_download,
        hf_hub_url,
        get_hf_file_metadata,
        progress_cb=None,
        start_percent: int = 0,
        end_percent: int = 100,
        label: str = "Downloading file...",
    ) -> str:
        expected_path = os.path.join(local_dir, filename.replace("/", os.sep))
        try:
            file_url = hf_hub_url(repo_id=repo_id, filename=filename, revision=revision)
            metadata = get_hf_file_metadata(url=file_url)
            expected_size = int(getattr(metadata, "size", 0) or 0)
        except Exception:
            expected_size = 0

        stop_event = threading.Event()

        def _emit_progress(raw_percent: int, message: str) -> None:
            if not progress_cb:
                return
            scaled = start_percent + int(((end_percent - start_percent) * max(0, min(100, raw_percent))) / 100)
            progress_cb(scaled, message)

        def _watch_file() -> None:
            last_percent = -1
            while not stop_event.is_set():
                current_size = 0
                try:
                    if os.path.exists(expected_path):
                        current_size = os.path.getsize(expected_path)
                    elif os.path.exists(expected_path + ".incomplete"):
                        current_size = os.path.getsize(expected_path + ".incomplete")
                except Exception:
                    current_size = 0
                if expected_size > 0:
                    raw_percent = int((current_size / expected_size) * 100)
                    raw_percent = max(0, min(99, raw_percent))
                    if raw_percent != last_percent:
                        _emit_progress(raw_percent, f"{label} ({raw_percent}%)")
                        last_percent = raw_percent
                elif last_percent != -2:
                    if progress_cb:
                        progress_cb(-1, label)
                    last_percent = -2
                time.sleep(0.2)

        watcher = threading.Thread(target=_watch_file, daemon=True)
        watcher.start()
        try:
            downloaded = hf_hub_download(
                repo_id=repo_id,
                revision=revision,
                filename=filename,
                local_dir=local_dir,
            )
        finally:
            stop_event.set()
            watcher.join(timeout=1.0)
        _emit_progress(100, f"{label} (100%)")
        return downloaded

    def _cleanup_empty_voice_cache_dirs(self, start_dir: str) -> None:
        base_dir = os.path.normcase(os.path.abspath(join_root("models")))
        current = os.path.abspath(str(start_dir or ""))
        while current and os.path.normcase(current).startswith(base_dir):
            try:
                if os.path.isdir(current) and not os.listdir(current):
                    os.rmdir(current)
                    parent = os.path.dirname(current)
                    if parent == current:
                        break
                    current = parent
                    continue
            except Exception:
                break
            break

    def _piper_voice_entries(self, language: str = "") -> list[dict]:
        payload = self._read_catalog()
        items: list[dict] = []
        language = str(language or "").strip().lower()
        for voice in payload.get("voices", []) or []:
            if not isinstance(voice, dict):
                continue
            if str(voice.get("provider", "")).strip().lower() != "piper":
                continue
            voice_id = str(voice.get("id", "")).strip()
            if not voice_id:
                continue
            voice_language = str(voice.get("language", "")).strip().lower().split("-", 1)[0]
            if language and voice_language != language:
                continue
            items.append(voice)
        return items

    def _voice_pack_status(self, language: str = "") -> str:
        entries = self._piper_voice_entries(language)
        if not entries:
            return "missing"
        installed = sum(1 for entry in entries if self.is_resource_installed(f"voice:{str(entry.get('id', '')).strip()}"))
        if installed <= 0:
            return "missing"
        if installed >= len(entries):
            return "installed"
        return "partial"

    def _usable_piper_voice_count(self, language: str = "") -> int:
        """Count local Piper voices that can actually be loaded.

        Resource-pack completeness is not meaningful to users: they may
        intentionally install only a subset or add their own voices. A voice
        is usable when its ONNX model and matching JSON config are both
        present in the language's storage folder.
        """
        normalized_language = str(language or "").strip().lower().split("-", 1)[0]
        folder_name = "piper-en" if normalized_language == "en" else "piper"
        roots = [
            os.path.join(self.workspace_root, "models", folder_name),
            os.path.join(bundle_root(), "models", folder_name),
        ]
        seen_names: set[str] = set()
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                for model_path in Path(root).rglob("*.onnx"):
                    if not model_path.is_file() or model_path.stat().st_size <= 0:
                        continue
                    config_path = Path(f"{model_path}.json")
                    if not config_path.is_file() or config_path.stat().st_size <= 0:
                        continue
                    seen_names.add(str(model_path.name).lower())
            except OSError:
                continue
        return len(seen_names)

    def _whisper_cache_root(self) -> str:
        return models_path("faster_whisper")

    def _speaker_diarization_root(self) -> str:
        return models_path("pyannote")

    def _speaker_diarization_segmentation_path(self) -> str:
        return os.path.join(
            self._speaker_diarization_root(),
            "model.int8.onnx",
        )

    def _speaker_diarization_embedding_path(self) -> str:
        return os.path.join(
            self._speaker_diarization_root(),
            "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
        )

    def _whisper_cache_dirs(self, model_name: str) -> list[str]:
        root = Path(self._whisper_cache_root())
        if not root.exists():
            return []
        normalized = str(model_name or "").strip().lower()
        matches: list[str] = []
        for child in root.iterdir():
            if not child.is_dir():
                continue
            name = child.name.lower()
            if normalized == name:
                matches.append(str(child))
                continue
            if name.startswith("models--") and normalized in name:
                matches.append(str(child))
        return matches

    _OCR_REQUIRED_MODELS = [
        "ch_PP-OCRv4_det_mobile.onnx",
        "ch_PP-OCRv4_rec_mobile.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "ppocr_keys_v1.txt",
    ]

    def _ocr_model_dir(self) -> str:
        # Do not make a lightweight availability check depend on importing the
        # entire RapidOCR runtime. In a frozen build a missing lazy submodule
        # used to turn an import exception into the misleading message
        # "Rapid OCR engine missing", even when all bundled models existed.
        candidates = [
            os.path.join(bundle_root(), "rapidocr"),
            os.path.join(self.workspace_root, "rapidocr"),
        ]
        import sys
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            candidates.append(os.path.join(meipass, "rapidocr"))
        for candidate in candidates:
            if os.path.isdir(os.path.join(candidate, "models")):
                return candidate
        try:
            import rapidocr
            models_dir = os.path.dirname(rapidocr.__file__)
            if models_dir and os.path.isdir(os.path.join(models_dir, "models")):
                return models_dir
        except Exception:
            pass
        return ""

    def _ocr_model_status(self) -> str:
        models_dir = self._ocr_model_dir()
        if not models_dir:
            return "missing"
        models_path_dir = os.path.join(models_dir, "models")
        if not os.path.isdir(models_path_dir):
            return "missing"
        # Dynamically detect PP-OCR models (PP-OCRv4 / PP-OCRv6)
        onnx_models = [f for f in os.listdir(models_path_dir) if f.endswith(".onnx")]
        has_det = any("det" in f.lower() for f in onnx_models)
        has_rec = any("rec" in f.lower() for f in onnx_models)
        if has_det and has_rec:
            return "installed"
        return "missing"

    def is_ocr_ready(self) -> bool:
        return self._ocr_model_status() == "installed"


    @staticmethod
    def is_sensevoice_runtime_ready() -> bool:
        """Verify the bundled runtime can be imported before entering editor."""
        try:
            import sherpa_onnx  # noqa: F401
            return True
        except Exception:
            return False

    def validate_sensevoice_runtime(self) -> list[tuple[str, str]]:
        """Return actionable first-run checks for the bundled default ASR."""
        issues: list[tuple[str, str]] = []
        model_dir = models_path("sensevoice")
        model_path = os.path.join(model_dir, "model.int8.onnx")
        tokens_path = os.path.join(model_dir, "tokens.txt")
        if not os.path.isfile(model_path):
            issues.append(("sensevoice:model", f"SenseVoice model is missing: {model_path}"))
        if not os.path.isfile(tokens_path):
            issues.append(("sensevoice:tokens", f"SenseVoice tokens file is missing: {tokens_path}"))
        try:
            import sherpa_onnx  # noqa: F401
        except Exception as exc:
            issues.append(("sensevoice:runtime", f"SenseVoice runtime could not load: {exc}"))
        silero_path = bin_path("silero_vad.onnx")
        if not os.path.isfile(silero_path):
            issues.append(("sensevoice:vad", f"Silero VAD model is missing: {silero_path}"))
        return issues

    def validate_ocr_runtime(self) -> list[tuple[str, str]]:
        """Verify selected OCR can start, not just that a model folder exists."""
        issues: list[tuple[str, str]] = []
        model_dir = self._ocr_model_dir()
        if self._ocr_model_status() != "installed":
            issues.append(("ocr:models", f"RapidOCR models are missing from: {model_dir or 'bundled OCR resources'}"))
        try:
            from rapidocr import RapidOCR  # noqa: F401
        except Exception as exc:
            issues.append(("ocr:runtime", f"RapidOCR runtime could not load: {exc}"))
        return issues

    def validate_piper_voice_runtime(self, voice_id: str) -> list[tuple[str, str]]:
        """Check the chosen local Piper voice before a default Both run."""
        voice_id = str(voice_id or "").strip()
        if not voice_id or voice_id.startswith(("edge:", "f5:")):
            return []
        issues: list[tuple[str, str]] = []
        entry = self._find_voice_entry(voice_id)
        if not entry:
            return [(f"voice:{voice_id}", f"Selected local voice is not available: {voice_id}")]
        model_path, config_path = self._voice_local_paths(entry)
        if not os.path.isfile(model_path):
            issues.append((f"voice:{voice_id}:model", f"Piper voice model is missing: {model_path}"))
        if not os.path.isfile(config_path):
            issues.append((f"voice:{voice_id}:config", f"Piper voice config is missing: {config_path}"))
        try:
            from piper import PiperVoice  # noqa: F401
        except Exception as exc:
            issues.append(("piper:runtime", f"Piper runtime could not load: {exc}"))
        return issues

    def validate_pipeline_runtime(self) -> list[tuple[str, str]]:
        """Check local executables and writable working folders before a worker starts."""
        issues: list[tuple[str, str]] = []
        ffmpeg_path = bin_path("ffmpeg", "ffmpeg.exe")
        if not os.path.isfile(ffmpeg_path):
            issues.append(("ffmpeg", f"FFmpeg is missing: {ffmpeg_path}"))
        else:
            try:
                result = subprocess.run(
                    [ffmpeg_path, "-version"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    **subprocess_hidden_kwargs(),
                )
                if result.returncode != 0:
                    issues.append(("ffmpeg", "FFmpeg could not start. Reinstall or re-extract CapCap."))
            except Exception as exc:
                issues.append(("ffmpeg", f"FFmpeg could not start: {exc}"))

        # A GUI can launch from a protected folder, then fail only when the
        # worker first writes project/temp output. Detect that exact case up
        # front and give the user a recovery path instead of a generic 500.
        for directory_name in ("projects", "temp"):
            target_dir = os.path.join(self.workspace_root, directory_name)
            probe_path = os.path.join(target_dir, f".capcap_write_probe_{uuid.uuid4().hex}")
            try:
                os.makedirs(target_dir, exist_ok=True)
                with open(probe_path, "x", encoding="utf-8") as handle:
                    handle.write("ok")
                os.remove(probe_path)
            except OSError as exc:
                try:
                    if os.path.exists(probe_path):
                        os.remove(probe_path)
                except OSError:
                    pass
                issues.append((
                    f"workspace:{directory_name}",
                    f"CapCap cannot write its {directory_name} folder ({target_dir}): {exc}. "
                    "Move CapCap to a writable folder or adjust folder permissions.",
                ))
        return issues

    @staticmethod
    def is_nvidia_driver_available() -> bool:
        import subprocess
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10,
                **subprocess_hidden_kwargs(),
            )
            if result.returncode == 0 and result.stdout.strip():
                return True
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                return True
        except Exception:
            pass
        return False

    def get_device_requirements(self, device: str) -> list[tuple[str, str]]:
        dev = str(device or "").strip().lower()
        if dev == "cpu":
            return [
                # SenseVoice is the bundled default transcription engine.
                # RapidOCR is validated only when the user explicitly picks
                # OCR as the subtitle source.
                ("sensevoice:model", "SenseVoice model"),
                ("sensevoice:runtime", "SenseVoice runtime"),
            ]
        return [
            ("sensevoice:model", "SenseVoice model"),
            ("sensevoice:runtime", "SenseVoice runtime"),
            ("cuda:whisper", "CUDA runtime pack"),
            ("nvidia_driver", "NVIDIA driver"),
        ]

    def is_requirement_met(self, requirement_id: str) -> bool:
        rid = str(requirement_id or "").strip()
        if rid == "ocr":
            return self.is_ocr_ready()
        if rid == "sensevoice:runtime":
            return self.is_sensevoice_runtime_ready()
        if rid == "nvidia_driver":
            return self.is_nvidia_driver_available()
        return self.is_resource_installed(rid)

    def validate_device(self, device: str) -> tuple[bool, list[tuple[str, str]]]:
        missing: list[tuple[str, str]] = []
        for rid, label in self.get_device_requirements(device):
            if not self.is_requirement_met(rid):
                missing.append((rid, label))
        return (len(missing) == 0, missing)

    def _hf_blob_url(self, filename: str) -> str:
        return (
            f"https://huggingface.co/{self.HF_RESOURCE_REPO}/"
            f"resolve/{self.HF_RESOURCE_REVISION}/{filename.lstrip('/')}"
        )

    def list_resources(self) -> list[dict]:
        resources: list[dict] = [
            {
                "id": "sensevoice:model",
                "name": "SenseVoice Small (Chinese ASR)",
                "kind": "sensevoice",
                "status": "installed" if self.is_resource_installed("sensevoice:model") else "missing",
                "target_dir": models_path("sensevoice"),
                "download_url": "https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
                "expected_filename": "model.int8.onnx",
                "auto_download_supported": True,
                "description": "Fast, non-autoregressive speech recognition model for Chinese video transcription.",
            },
            {
                "id": "vad:silero",
                "name": "Silero VAD (Sherpa-ONNX)",
                "kind": "sensevoice",
                "status": "installed" if self.is_resource_installed("vad:silero") else "missing",
                "target_dir": join_root("bin"),
                "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx",
                "expected_filename": "silero_vad.onnx",
                "auto_download_supported": True,
                "description": "Voice activity detector model for accurate speech segmentation.",
            },
            {
                "id": "whisper:base",
                "name": "Whisper Base",
                "kind": "whisper_cpu",
                "status": "installed" if self.is_resource_installed("whisper:base") else "missing",
                "target_dir": self._whisper_cache_root(),
                "download_url": "https://huggingface.co/Hacht/CapCapResource/blob/main/zipResource/models--Systran--faster-whisper-base.zip",
                "expected_filename": self.WHISPER_ZIP_FILES["base"],
                "auto_download_supported": False,
                "description": "Speech-recognition model for CPU transcription.",
            },
            {
                "id": "whisper:small",
                "name": "Whisper Small",
                "kind": "whisper_cpu",
                "status": "installed" if self.is_resource_installed("whisper:small") else "missing",
                "target_dir": self._whisper_cache_root(),
                "download_url": "https://huggingface.co/Hacht/CapCapResource/blob/main/zipResource/models--Systran--faster-whisper-small.zip",
                "expected_filename": self.WHISPER_ZIP_FILES["small"],
                "auto_download_supported": False,
                "description": "Faster speech-recognition model for CPU transcription.",
            },
            {
                "id": "whisper:medium",
                "name": "Whisper Medium",
                "kind": "whisper",
                "status": "installed" if self.is_resource_installed("whisper:medium") else "missing",
                "target_dir": self._whisper_cache_root(),
                "download_url": self._hf_blob_url("zipResource/models--Systran--faster-whisper-medium.zip"),
                "expected_filename": "models--Systran--faster-whisper-medium.zip",
                "auto_download_supported": False,
                "description": "Speech-recognition model used to create the original transcript.",
            },
            {
                "id": "cuda:whisper",
                "name": "GPU Acceleration Pack (CUDA 12, ~1.6 GB)",
                "kind": "cuda",
                "required_for": "GPU Mode",
                "status": "installed" if self.is_resource_installed("cuda:whisper") else "missing",
                "target_dir": join_root("bin", "cuda12_fw"),
                "download_url": self._hf_blob_url("zipResource/cuda12_fw.zip"),
                "expected_filename": "cuda12_fw.zip",
                "auto_download_supported": True,
                "description": "Required for GPU Mode. Provides the CUDA runtime used to accelerate supported local processing.",
            },
            {
                "id": "diarization:segmentation",
                "name": "Speaker Diarization Segmentation (Sherpa-ONNX)",
                "kind": "diarization",
                "status": "installed" if self.is_resource_installed("diarization:segmentation") else "missing",
                "target_dir": self._speaker_diarization_root(),
                "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                "expected_filename": "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                "auto_download_supported": False,
                "description": "ONNX model that detects potential speaker changes.",
            },
            {
                "id": "diarization:embedding",
                "name": "Speaker Diarization Embedding (3D-Speaker)",
                "kind": "diarization",
                "status": "installed" if self.is_resource_installed("diarization:embedding") else "missing",
                "target_dir": self._speaker_diarization_root(),
                "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                "expected_filename": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                "auto_download_supported": False,
                "description": "ONNX model that identifies and groups speaker voices.",
            },
        ]

        vietnamese_entries = self._piper_voice_entries("vi")
        vietnamese_count = self._usable_piper_voice_count("vi")
        if vietnamese_entries or vietnamese_count:
            count = vietnamese_count
            resources.append(
                {
                    "id": "voice:pack",
                    "name": "Vietnamese Voices (Piper)",
                    "kind": "voice",
                    "status": "installed" if count else "missing",
                    "status_label": f"{count} voice{'s' if count != 1 else ''} available",
                    "target_dir": models_path("piper"),
                    "download_url": self._hf_blob_url("zipResource/piper.zip"),
                    "expected_filename": "piper.zip",
                    "auto_download_supported": True,
                    "description": "Offline Vietnamese voices detected in the Piper storage folder.",
                }
            )
        english_entries = self._piper_voice_entries("en")
        english_count = self._usable_piper_voice_count("en")
        if english_entries or english_count:
            count = english_count
            resources.append(
                {
                    "id": "voice:pack-en",
                    "name": "English Voices (Piper)",
                    "kind": "voice",
                    "status": "installed" if count else "missing",
                    "status_label": f"{count} voice{'s' if count != 1 else ''} available",
                    "target_dir": models_path("piper-en"),
                    "download_url": self._hf_blob_url("zipResource/piper-en.zip"),
                    "expected_filename": "piper-en.zip",
                    "auto_download_supported": True,
                    "description": "Offline English voices detected in the Piper storage folder.",
                }
            )
        return resources

    def is_resource_installed(self, resource_id: str) -> bool:
        if resource_id == "ocr:engine":
            return self.is_ocr_ready()
        if resource_id == "cuda:whisper":
            fw_dir = join_root("bin", "cuda12_fw")
            return os.path.exists(os.path.join(fw_dir, "cublas64_12.dll"))
        if resource_id == "sensevoice:model":
            sv_dir = models_path("sensevoice")
            has_tokens = os.path.isfile(os.path.join(sv_dir, "tokens.txt"))
            has_model = (
                os.path.isfile(os.path.join(sv_dir, "model.int8.onnx"))
                or os.path.isfile(os.path.join(sv_dir, "model.onnx"))
                or (os.path.isdir(sv_dir) and any(f.endswith(".onnx") for f in os.listdir(sv_dir)))
            )
            return has_tokens and has_model
        if resource_id == "vad:silero":
            vad_candidates = [
                os.path.join(bin_path(), "silero_vad.onnx"),
                os.path.join(models_path(), "silero_vad.onnx"),
                os.path.join(models_path("sensevoice"), "silero_vad.onnx"),
                join_root("bin", "silero_vad.onnx"),
            ]
            return any(os.path.isfile(c) and os.path.getsize(c) > 0 for c in vad_candidates)
        if resource_id == "diarization:segmentation":
            return os.path.isfile(self._speaker_diarization_segmentation_path())
        if resource_id == "diarization:embedding":
            return os.path.isfile(self._speaker_diarization_embedding_path())

        if resource_id.startswith("whisper:"):
            model_name = resource_id.split(":", 1)[1].strip().lower()
            for model_dir in self._whisper_cache_dirs(model_name):
                try:
                    if os.path.isdir(model_dir) and any(Path(model_dir).iterdir()):
                        return True
                except Exception:
                    continue
            return False
        if resource_id == "voice:pack":
            return self._voice_pack_status("vi") == "installed"
        if resource_id == "voice:pack-en":
            return self._voice_pack_status("en") == "installed"
        if resource_id.startswith("voice:"):
            voice_id = resource_id.split(":", 1)[1].strip()
            voice_entry = self._find_voice_entry(voice_id)
            if not voice_entry:
                return False
            model_path, config_path = self._voice_local_paths(voice_entry)
            return os.path.exists(model_path) and os.path.exists(config_path)
        if resource_id == "cuda:ort":
            try:
                import onnxruntime
                return os.path.isfile(
                    os.path.join(os.path.dirname(onnxruntime.__file__), "capi", "onnxruntime_providers_cuda.dll")
                )
            except Exception:
                return False
        return False

    def _find_voice_entry(self, voice_id: str) -> dict | None:
        payload = self._read_catalog()
        for voice in payload.get("voices", []) or []:
            if isinstance(voice, dict) and str(voice.get("id", "")).strip() == voice_id:
                return voice
        return None

    def _download_and_extract_zip(self, zip_url: str, extract_to: str, progress_cb=None) -> None:
        import tempfile
        import urllib.request
        import zipfile

        print(f"[Download] Starting download: {zip_url}")
        print(f"[Download] Target directory: {extract_to}")
        os.makedirs(extract_to, exist_ok=True)

        with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp_file:
            tmp_path = tmp_file.name

        try:
            if progress_cb:
                progress_cb(-1, "Downloading zip file...")

            def _report_progress(block_num, block_size, total_size):
                if progress_cb and total_size > 0:
                    downloaded = block_num * block_size
                    percent = min(99, int((downloaded / total_size) * 100))
                    progress_cb(percent, f"Downloading... ({percent}%)")
                if block_num % 10 == 0:  # Log every 10 blocks
                    print(f"[Download] Progress: block {block_num}, size {block_size}, total {total_size}")

            print(f"[Download] Calling urlretrieve...")
            urllib.request.urlretrieve(zip_url, tmp_path, reporthook=_report_progress)
            print(f"[Download] Download complete. File size: {os.path.getsize(tmp_path)} bytes")

            if progress_cb:
                progress_cb(90, "Extracting zip file...")

            print(f"[Download] Extracting zip to {extract_to}...")
            with zipfile.ZipFile(tmp_path, "r") as zip_ref:
                zip_ref.extractall(extract_to)
            print(f"[Download] Extraction complete.")

            if progress_cb:
                progress_cb(100, "Extraction complete.")
        except Exception as e:
            print(f"[Download] ERROR: {e}")
            raise
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def download_resource(self, resource_id: str, progress_cb=None) -> None:
        if resource_id.startswith("whisper:"):
            raise ValueError(
                "Whisper models are downloaded manually. Use Open Download Page, then extract the ZIP into models/faster_whisper."
            )

        if resource_id == "cuda:whisper":
            zip_url = self._hf_blob_url("zipResource/cuda12_fw.zip")
            target_dir = join_root("bin")
            self._download_and_extract_zip(zip_url, target_dir, progress_cb)
            try:
                from huggingface_hub import hf_hub_download, hf_hub_url
                from huggingface_hub.file_download import get_hf_file_metadata
            except Exception:
                pass
            ort_dir = ""
            try:
                import onnxruntime
                ort_dir = os.path.join(os.path.dirname(onnxruntime.__file__), "capi")
            except Exception:
                pass
            if ort_dir and os.path.isdir(ort_dir):
                target_ort = os.path.join(ort_dir, "onnxruntime_providers_cuda.dll")
                if not os.path.isfile(target_ort):
                    try:
                        downloaded = self._download_hf_file(
                            repo_id=self.repo_id,
                            revision=self.revision,
                            filename="onnxruntime/capi/onnxruntime_providers_cuda.dll",
                            local_dir=os.path.dirname(os.path.dirname(ort_dir)),
                            hf_hub_download=hf_hub_download,
                            hf_hub_url=hf_hub_url,
                            get_hf_file_metadata=get_hf_file_metadata,
                            progress_cb=progress_cb,
                            start_percent=0,
                            end_percent=100,
                            label="Downloading onnxruntime_providers_cuda.dll",
                        )
                        if downloaded and os.path.isfile(downloaded):
                            norm_src = os.path.normcase(os.path.abspath(downloaded))
                            norm_dst = os.path.normcase(os.path.abspath(target_ort))
                            if norm_src != norm_dst:
                                if os.path.exists(target_ort):
                                    os.remove(target_ort)
                                os.makedirs(ort_dir, exist_ok=True)
                                shutil.move(downloaded, target_ort)
                    except Exception as e:
                        print(f"[CUDA] Failed to download ONNX GPU provider: {e}")
            if progress_cb:
                progress_cb(100, "GPU runtime is ready.")
            return

        if resource_id.startswith("voice:"):
            if resource_id == "voice:pack":
                zip_url = self._hf_blob_url("zipResource/piper.zip")
                target_dir = models_path("piper")
                self._download_and_extract_zip(zip_url, target_dir, progress_cb)
                return
            if resource_id == "voice:pack-en":
                zip_url = self._hf_blob_url("zipResource/piper-en.zip")
                target_dir = models_path("piper-en")
                self._download_and_extract_zip(zip_url, target_dir, progress_cb)
                return

            voice_id = resource_id.split(":", 1)[1].strip()
            voice_entry = self._find_voice_entry(voice_id)
            if not voice_entry:
                raise ValueError(f"Voice '{voice_id}' was not found in catalog.")
            remote_model, remote_config = self._voice_remote_paths(voice_entry)
            if progress_cb:
                progress_cb(10, f"Downloading voice model: {voice_id}...")
            try:
                from huggingface_hub import hf_hub_download
            except Exception as exc:
                raise ImportError(
                    "huggingface_hub is not installed. Run `pip install huggingface_hub` first."
                ) from exc
            model_download = hf_hub_download(
                repo_id=self.repo_id,
                revision=self.revision,
                filename=remote_model,
                local_dir=join_root("models"),
            )
            self._finalize_voice_download(model_download, voice_entry, is_config=False)
            if progress_cb:
                progress_cb(60, f"Downloading voice config: {voice_id}...")
            config_download = hf_hub_download(
                repo_id=self.repo_id,
                revision=self.revision,
                filename=remote_config,
                local_dir=join_root("models"),
            )
            self._finalize_voice_download(config_download, voice_entry, is_config=True)
            if progress_cb:
                progress_cb(100, f"Voice {voice_id} is ready.")
            return

        if resource_id == "sensevoice:model":
            target_dir = models_path("sensevoice")
            os.makedirs(target_dir, exist_ok=True)
            if progress_cb:
                progress_cb(5, "Downloading SenseVoice model (~120MB)...")
            try:
                from huggingface_hub import hf_hub_download
                m_path = hf_hub_download(
                    repo_id=self.SENSEVOICE_REPO,
                    filename="model.int8.onnx",
                    local_dir=target_dir,
                )
                if progress_cb:
                    progress_cb(80, "Downloading SenseVoice vocabulary tokens...")
                t_path = hf_hub_download(
                    repo_id=self.SENSEVOICE_REPO,
                    filename="tokens.txt",
                    local_dir=target_dir,
                )
            except Exception as exc:
                print(f"[SenseVoice Download] HF download failed: {exc}")
                raise
            if progress_cb:
                progress_cb(100, "SenseVoice model is ready.")
            return

        if resource_id == "vad:silero":
            import urllib.request
            target_file = os.path.join(join_root("bin"), "silero_vad.onnx")
            os.makedirs(os.path.dirname(target_file), exist_ok=True)
            if progress_cb:
                progress_cb(10, "Downloading Silero VAD model...")
            url = "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx"
            try:
                def _vad_rep(block_num, block_size, total_size):
                    if progress_cb and total_size > 0:
                        downloaded = block_num * block_size
                        percent = min(99, int((downloaded / total_size) * 100))
                        progress_cb(percent, f"Downloading Silero VAD ({percent}%)...")

                urllib.request.urlretrieve(url, target_file, reporthook=_vad_rep)
            except Exception as exc:
                print(f"[VAD Download] Direct URL failed: {exc}, trying HuggingFace fallback...")
                try:
                    from huggingface_hub import hf_hub_download
                    downloaded = hf_hub_download(
                        repo_id=self.SENSEVOICE_REPO,
                        filename="silero_vad.onnx",
                        local_dir=join_root("bin"),
                    )
                    if os.path.isfile(downloaded) and os.path.abspath(downloaded) != os.path.abspath(target_file):
                        shutil.copy2(downloaded, target_file)
                except Exception as hf_exc:
                    raise RuntimeError(f"Failed to download Silero VAD: {exc} | {hf_exc}") from hf_exc

            if progress_cb:
                progress_cb(100, "Silero VAD model is ready.")
            return

        if resource_id in {self.NORMAL_AI_RESOURCE_ID, self.HIGH_AI_RESOURCE_ID}:
            raise ValueError(
                f"Auto download is not supported for '{resource_id}'. Use 'Open Download Page' to get the file manually."
            )

        raise ValueError(f"Unsupported resource: {resource_id}")

