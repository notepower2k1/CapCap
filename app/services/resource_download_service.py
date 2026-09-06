from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import uuid
import urllib.request
import zipfile
import tarfile
from pathlib import Path

from runtime_paths import app_path, bin_path, bundle_root, join_root, models_path, subprocess_hidden_kwargs, subprocess_text_kwargs


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
    # Keep the accepted model sets here (instead of relying only on the
    # Python package's import) so a packaged build can recover missing model
    # data through Manage Resources.
    # Resource packs built for older RapidOCR releases contain PP-OCRv4
    # files, while current RapidOCR wheels bundle PP-OCRv6 files.  Both are
    # valid model sets; checking one fixed filename list made a working
    # bundled installation appear as "Missing" in Manage Resources.
    _OCR_MODEL_SETS = (
        (
            "PP-OCRv4",
            (
                "ch_PP-OCRv4_det_mobile.onnx",
                "ch_PP-OCRv4_rec_mobile.onnx",
                "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                "ppocr_keys_v1.txt",
            ),
        ),
        (
            "PP-OCRv6",
            (
                "PP-OCRv6_det_small.onnx",
                "PP-OCRv6_rec_small.onnx",
                "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
            ),
        ),
    )
    # Keep this alias for the download branch, which intentionally downloads
    # the compact PP-OCRv4 pack from the CapCap resource repository.
    _OCR_REQUIRED_MODELS = _OCR_MODEL_SETS[0][1]
    _AUTO_DOWNLOAD_IDS = {
        "whisper:base",
        "whisper:small",
        "whisper:medium",
        "sensevoice:model",
        "ocr:engine",
        "cuda:whisper",
        "diarization:segmentation",
        "diarization:embedding",
        "voice:pack",
        "voice:pack-en",
        "voice:vieneu",
    }

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
        # Piper's legacy packs keep one JSON file beside every ONNX model,
        # while the current piper-new Vietnamese pack keeps one shared
        # config.json in models/piper.  Prefer the legacy file when present,
        # then fall back to the shared directory config.
        legacy_config = f"{model_path}.json"
        model_dir_name = os.path.basename(os.path.dirname(model_path)) or "piper"
        shared_config = models_path(model_dir_name, "config.json")
        if model_dir_name.lower() == "piper" and os.path.isfile(shared_config):
            config_path = shared_config
        elif os.path.isfile(legacy_config):
            config_path = legacy_config
        elif model_dir_name.lower() == "piper" or os.path.isfile(shared_config):
            # piper-new uses this shared path even before config.json has
            # been downloaded; returning the expected target lets an
            # individual voice download place it correctly.
            config_path = shared_config
        else:
            config_path = legacy_config
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
        # Vietnamese voices in the new resource pack live directly under
        # piper-new and all use its single shared config.json.  Keep the
        # existing piper-en archive layout for English voices.
        if remote_model.startswith("piper/") and not remote_model.startswith("piper-en/"):
            remote_model = f"piper-new/{model_name}"
            remote_config = "piper-new/config.json"
        else:
            remote_config = f"{remote_model}.json"
        return (
            remote_model,
            remote_config,
        )

    def _finalize_voice_download(self, downloaded_path: str, voice_entry: dict, *, is_config: bool) -> str:
        source_path = str(downloaded_path or "").strip()
        if not source_path or not os.path.exists(source_path):
            return source_path
        model_path, config_path = self._voice_local_paths(voice_entry)
        # Never write a downloaded voice into PyInstaller's read-only
        # _internal directory.  Resolve a deterministic writable destination
        # from the catalog path, including the shared piper-new config.
        provider_voice = str(voice_entry.get("provider_voice", "")).strip().replace("\\", "/")
        relative_voice = provider_voice[len("models/"):] if provider_voice.startswith("models/") else ""
        if relative_voice.startswith(("piper/", "piper-en/")):
            relative_parts = Path(relative_voice).parts
            writable_model = join_root("models", *relative_parts)
            if is_config:
                target_path = (
                    join_root("models", "piper", "config.json")
                    if relative_voice.startswith("piper/")
                    else f"{writable_model}.json"
                )
            else:
                target_path = writable_model
        else:
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
        known_ids: set[str] = set()
        language = str(language or "").strip().lower()
        for voice in payload.get("voices", []) or []:
            if not isinstance(voice, dict):
                continue
            if str(voice.get("provider", "")).strip().lower() != "piper":
                continue
            voice_id = str(voice.get("id", "")).strip()
            if not voice_id:
                continue
            known_ids.add(voice_id)
            voice_language = str(voice.get("language", "")).strip().lower().split("-", 1)[0]
            if language and voice_language != language:
                continue
            items.append(voice)

        # The packaged catalog may be read-only and may predate additional
        # models listed by the shared piper-new manifest. Add those entries
        # in memory so resource checks and voice validation see the same set
        # as the UI voice selector.
        manifest_path = models_path("piper", "voices.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                    manifest = json.load(handle)
            except Exception:
                manifest = []
            if isinstance(manifest, list):
                for metadata in manifest:
                    if not isinstance(metadata, dict):
                        continue
                    audio_path = str(metadata.get("audio_path", "") or "").replace("\\", "/").strip()
                    filename = os.path.basename(audio_path)
                    voice_id = os.path.splitext(filename)[0]
                    if not voice_id or voice_id in known_ids:
                        continue
                    model_path = models_path("piper", filename)
                    if not os.path.isfile(model_path) or os.path.getsize(model_path) <= 0:
                        continue
                    manifest_entry = {
                        "id": voice_id,
                        "name": str(metadata.get("name", "") or "").strip() or voice_id,
                        "provider": "piper",
                        "provider_voice": f"models/piper/{filename}",
                        "language": "vi",
                        "gender": str(metadata.get("gender", "") or "").strip(),
                        "tier": "free",
                        "enabled": True,
                        "tags": ["local", "piper"],
                    }
                    if str(metadata.get("description", "") or "").strip():
                        manifest_entry["description"] = str(metadata.get("description")).strip()
                    known_ids.add(voice_id)
                    voice_language = "vi"
                    if not language or voice_language == language:
                        items.append(manifest_entry)
        return items

    def _voice_pack_status(self, language: str = "") -> str:
        available_count = self._usable_piper_voice_count(language)
        # Piper resources are intentionally treated as an available-voice
        # count rather than a fixed completeness checklist. Users may install
        # any subset of piper-new or add custom voices.
        return "installed" if available_count else "missing"

    def _usable_piper_voice_count(self, language: str = "") -> int:
        """Count local Piper voices that can actually be loaded.

        Resource-pack completeness is not meaningful to users: they may
        intentionally install only a subset or add their own voices. A voice
        is usable when its ONNX model and either a matching per-model JSON
        config or a shared directory config are present.
        """
        normalized_language = str(language or "").strip().lower().split("-", 1)[0]
        folder_name = "piper-en" if normalized_language == "en" else "piper"
        roots = [
            os.path.join(self.workspace_root, "models", folder_name),
            os.path.join(bundle_root(), "models", folder_name),
        ]
        # A pack can be split between the writable install directory and the
        # read-only PyInstaller bundle.  A shared config from either root is
        # valid for every model in that language folder.
        shared_configs = {
            str(Path(root) / "config.json")
            for root in roots
            if os.path.isfile(os.path.join(root, "config.json"))
            and os.path.getsize(os.path.join(root, "config.json")) > 0
        }
        seen_names: set[str] = set()
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                for model_path in Path(root).rglob("*.onnx"):
                    if not model_path.is_file() or model_path.stat().st_size <= 0:
                        continue
                    config_path = Path(f"{model_path}.json")
                    has_legacy_config = config_path.is_file() and config_path.stat().st_size > 0
                    has_shared_config = bool(shared_configs)
                    if not has_legacy_config and not has_shared_config:
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

    def _sensevoice_model_dir(self) -> str:
        """Return a SenseVoice directory that contains a complete model.

        ``models_path()`` intentionally prefers the writable application
        directory.  A packaged build also creates an empty
        ``models/sensevoice`` folder for manual downloads, though, and that
        empty folder used to shadow the bundled model below ``_internal``.
        Prefer a directory containing both required files and only fall back
        to the first existing directory when the resource is incomplete so
        diagnostics still point at a useful location.
        """
        candidates = [
            os.path.join(self.workspace_root, "models", "sensevoice"),
            os.path.join(bundle_root(), "models", "sensevoice"),
        ]
        complete = []
        for candidate in candidates:
            if not os.path.isdir(candidate):
                continue
            if all(os.path.isfile(os.path.join(candidate, name)) for name in ("model.int8.onnx", "tokens.txt")):
                complete.append(candidate)
        if complete:
            return complete[0]
        for candidate in candidates:
            if os.path.isdir(candidate):
                return candidate
        return candidates[0]

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

    def _is_vieneu_installed(self) -> bool:
        """Check whether both VieNeu-TTS v3 Turbo and MOSS audio tokenizer are present."""
        candidates = [
            join_root("models", "vieneu", "hub"),
            models_path("vieneu", "hub"),
            join_root("models", "vieneu"),
            models_path("vieneu"),
            join_root("models", "huggingface", "hub"),
            models_path("huggingface", "hub"),
        ]
        hf_home = os.environ.get("HF_HOME", "").strip()
        if hf_home:
            candidates.append(os.path.join(hf_home, "hub"))
            candidates.append(hf_home)
        candidates.append(r"D:\CodingTime\TTS_Resource\huggingface\hub")
        candidates.append(os.path.join(str(Path.home()), ".cache", "huggingface", "hub"))

        for hub in candidates:
            if not os.path.isdir(hub):
                continue
            tok_dir = os.path.join(hub, "models--OpenMOSS-Team--MOSS-Audio-Tokenizer-Nano-ONNX", "snapshots")
            v3_dir = os.path.join(hub, "models--pnnbao-ump--VieNeu-TTS-v3-Turbo", "snapshots")
            if os.path.isdir(tok_dir) and os.path.isdir(v3_dir):
                try:
                    tok_snaps = [d for d in os.listdir(tok_dir) if os.path.isdir(os.path.join(tok_dir, d))]
                    v3_snaps = [d for d in os.listdir(v3_dir) if os.path.isdir(os.path.join(v3_dir, d))]
                    if tok_snaps and v3_snaps:
                        has_tok = any(os.listdir(os.path.join(tok_dir, s)) for s in tok_snaps)
                        has_v3 = any(os.listdir(os.path.join(v3_dir, s)) for s in v3_snaps)
                        if has_tok and has_v3:
                            return True
                except OSError:
                    continue
        return False

    def _ocr_model_dir(self) -> str:
        # Do not make a lightweight availability check depend on importing the
        # entire RapidOCR runtime. In a frozen build a missing lazy submodule
        # used to turn an import exception into the misleading message
        # "Rapid OCR engine missing", even when all bundled models existed.
        candidates = [
            # Current writable download location.
            os.path.join(self.workspace_root, "rapidocr"),
            # Older builds/documentation used models/rapidocr.
            os.path.join(self.workspace_root, "models", "rapidocr"),
            os.path.join(bundle_root(), "rapidocr"),
            os.path.join(bundle_root(), "models", "rapidocr"),
        ]
        import sys
        meipass = getattr(sys, "_MEIPASS", "") or ""
        if meipass:
            candidates.append(os.path.join(meipass, "rapidocr"))
            candidates.append(os.path.join(meipass, "models", "rapidocr"))
        first_model_dir = ""
        for candidate in candidates:
            model_dir = os.path.join(candidate, "models")
            if not os.path.isdir(model_dir):
                continue
            if not first_model_dir:
                first_model_dir = candidate
            if self._ocr_model_variant(model_dir):
                return candidate
        try:
            import rapidocr
            models_dir = os.path.dirname(rapidocr.__file__)
            if models_dir and os.path.isdir(os.path.join(models_dir, "models")):
                return models_dir
        except Exception:
            pass
        return first_model_dir

    @classmethod
    def _ocr_model_variant(cls, model_dir: str) -> str:
        """Return the installed PP-OCR model family, or an empty string."""
        for variant, required_files in cls._OCR_MODEL_SETS:
            if all(os.path.isfile(os.path.join(model_dir, name)) for name in required_files):
                return variant
        return ""

    def _ocr_model_status(self) -> str:
        models_dir = self._ocr_model_dir()
        if not models_dir:
            return "missing"
        models_path_dir = os.path.join(models_dir, "models")
        return "installed" if self._ocr_model_variant(models_path_dir) else "missing"

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
        model_dir = self._sensevoice_model_dir()
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
        if not voice_id or voice_id.startswith(("edge:", "f5:", "vieneu:", "vieneu_clone:", "capcut:")):
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
                capture_output=True, timeout=10,
                **subprocess_text_kwargs(),
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

    def supports_auto_download(self, resource_id: str) -> bool:
        """Whether Manage Resources can download this resource directly."""
        rid = str(resource_id or "").strip()
        return rid in self._AUTO_DOWNLOAD_IDS

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
                "id": "whisper:base",
                "name": "Whisper Base",
                "kind": "whisper_cpu",
                "status": "installed" if self.is_resource_installed("whisper:base") else "missing",
                "target_dir": join_root("models", "faster_whisper"),
                "download_url": self._hf_blob_url("zipResource/models--Systran--faster-whisper-base.zip"),
                "expected_filename": self.WHISPER_ZIP_FILES["base"],
                "auto_download_supported": True,
                "description": "Speech-recognition model for CPU transcription.",
            },
            {
                "id": "whisper:small",
                "name": "Whisper Small",
                "kind": "whisper_cpu",
                "status": "installed" if self.is_resource_installed("whisper:small") else "missing",
                "target_dir": join_root("models", "faster_whisper"),
                "download_url": self._hf_blob_url("zipResource/models--Systran--faster-whisper-small.zip"),
                "expected_filename": self.WHISPER_ZIP_FILES["small"],
                "auto_download_supported": True,
                "description": "Faster speech-recognition model for CPU transcription.",
            },
            {
                "id": "whisper:medium",
                "name": "Whisper Medium",
                "kind": "whisper",
                "status": "installed" if self.is_resource_installed("whisper:medium") else "missing",
                "target_dir": join_root("models", "faster_whisper"),
                "download_url": self._hf_blob_url("zipResource/models--Systran--faster-whisper-medium.zip"),
                "expected_filename": "models--Systran--faster-whisper-medium.zip",
                "auto_download_supported": True,
                "description": "Speech-recognition model used to create the original transcript.",
            },
            {
                "id": "cuda:whisper",
                "name": "GPU Acceleration Pack (CUDA 12.8, ~1.6 GB)",
                "kind": "cuda",
                "required_for": "GPU Mode",
                "status": "installed" if self.is_resource_installed("cuda:whisper") else "missing",
                "target_dir": join_root("bin", "cuda12_fw"),
                "link": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/cuda12_fw_new",
                "download_url": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/zipResource/cuda12_fw_new.zip",
                "open_url": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/zipResource/cuda12_fw_new.zip",
                "expected_filename": "cuda12_fw_new.zip",
                "auto_download_supported": True,
                "description": "Required for GPU Mode. Provides the CUDA 12.8 runtime used to accelerate supported local processing.",
            },
            {
                "id": "sensevoice:model",
                "name": "SenseVoice ASR Model (Sherpa-ONNX)",
                "kind": "sensevoice",
                "status": "installed" if self.is_resource_installed("sensevoice:model") else "missing",
                # Always point downloads at the writable application folder;
                # a bundled copy under _internal is read-only and should not
                # be overwritten.
                "target_dir": os.path.join(self.workspace_root, "models", "sensevoice"),
                "download_url": self._hf_blob_url("zipResource/sensevoice.zip"),
                "expected_filename": "model.int8.onnx + tokens.txt",
                "auto_download_supported": True,
                "description": (
                    "Optional recovery download for the bundled CPU speech-recognition model. "
                    "Use this when a packaged build cannot load its bundled SenseVoice files."
                ),
            },
            {
                "id": "ocr:engine",
                "name": "RapidOCR Models (PP-OCRv4 / PP-OCRv6)",
                "kind": "ocr",
                "status": "installed" if self.is_resource_installed("ocr:engine") else "missing",
                "target_dir": os.path.join(self.workspace_root, "rapidocr", "models"),
                "download_url": (
                    f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/rapidocr/models"
                ),
                "expected_filename": "PP-OCRv4 or PP-OCRv6 detector + recognizer + classifier",
                "auto_download_supported": True,
                "description": (
                    "OCR model files used by OCR mode and OCR Translator. A bundled PP-OCRv6 "
                    "set or the downloadable PP-OCRv4 pack is accepted; the RapidOCR runtime "
                    "remains bundled with the application."
                ),
            },
            {
                "id": "diarization:segmentation",
                "name": "Speaker Diarization Segmentation (Sherpa-ONNX)",
                "kind": "diarization",
                "status": "installed" if self.is_resource_installed("diarization:segmentation") else "missing",
                "target_dir": join_root("models", "pyannote"),
                "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                "expected_filename": "sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
                "auto_download_supported": True,
                "description": "ONNX model that detects potential speaker changes.",
            },
            {
                "id": "diarization:embedding",
                "name": "Speaker Diarization Embedding (3D-Speaker)",
                "kind": "diarization",
                "status": "installed" if self.is_resource_installed("diarization:embedding") else "missing",
                "target_dir": join_root("models", "pyannote"),
                "download_url": "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                "expected_filename": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
                "auto_download_supported": True,
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
                    "target_dir": join_root("models", "piper"),
                    "link": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/piper-new",
                    "download_url": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/zipResource/piper-new.zip",
                    "open_url": f"https://huggingface.co/{self.repo_id}/tree/{self.revision}/zipResource/piper-new.zip",
                    "expected_filename": "piper-new.zip",
                    "auto_download_supported": True,
                    "description": (
                        "Offline Vietnamese Piper voices. The piper-new pack uses one shared "
                        "config.json for every voice and is installed under models/piper."
                    ),
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
                    "target_dir": join_root("models", "piper-en"),
                    "download_url": self._hf_blob_url("zipResource/piper-en.zip"),
                    "expected_filename": "piper-en.zip",
                    "auto_download_supported": True,
                    "description": "Offline English voices detected in the Piper storage folder.",
                }
            )
        resources.append(
            {
                "id": "voice:vieneu",
                "name": "VieNeu-TTS Models (v3 Turbo ONNX)",
                "kind": "voice",
                "status": "installed" if self.is_resource_installed("voice:vieneu") else "missing",
                "target_dir": join_root("models", "vieneu"),
                "download_url": "https://huggingface.co/pnnbao-ump/VieNeu-TTS-v3-Turbo",
                "expected_filename": "VieNeu-TTS-v3-Turbo + MOSS-Audio-Tokenizer-Nano-ONNX",
                "auto_download_supported": True,
                "description": (
                    "Offline VieNeu-TTS v3 Turbo neural voice models (ONNX CPU) and MOSS audio tokenizer. "
                    "Supports natural Vietnamese speech synthesis and zero-shot voice cloning."
                ),
            }
        )
        return resources

    def is_resource_installed(self, resource_id: str) -> bool:
        if resource_id == "voice:capcut" or resource_id.startswith("voice:capcut:"):
            return True
        if resource_id == "voice:vieneu":
            return self._is_vieneu_installed()
        if resource_id == "ocr:engine":
            return self.is_ocr_ready()
        if resource_id == "cuda:whisper":
            for fw_name in ("cuda12_fw", "cuda12_fw_new"):
                fw_dir = join_root("bin", fw_name)
                if os.path.exists(os.path.join(fw_dir, "cublas64_12.dll")):
                    return True
            return False
        if resource_id == "sensevoice:model":
            model_dir = self._sensevoice_model_dir()
            return all(os.path.isfile(os.path.join(model_dir, name)) for name in ("model.int8.onnx", "tokens.txt"))
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
        for voice in self._piper_voice_entries():
            if str(voice.get("id", "")).strip() == voice_id:
                return voice
        return None

    def _download_and_extract_zip(self, zip_url: str, extract_to: str, progress_cb=None) -> None:
        import tempfile

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

    def _download_and_extract_tar(self, archive_url: str, extract_to: str, progress_cb=None) -> None:
        """Download and safely extract a tar/tar.bz2 resource archive."""
        import tempfile

        print(f"[Download] Starting download: {archive_url}")
        print(f"[Download] Target directory: {extract_to}")
        os.makedirs(extract_to, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar.bz2") as tmp_file:
            tmp_path = tmp_file.name
        try:
            if progress_cb:
                progress_cb(-1, "Downloading archive...")

            def _report_progress(block_num, block_size, total_size):
                if progress_cb and total_size > 0:
                    downloaded = block_num * block_size
                    percent = min(99, int((downloaded / total_size) * 100))
                    progress_cb(percent, f"Downloading... ({percent}%)")

            urllib.request.urlretrieve(archive_url, tmp_path, reporthook=_report_progress)
            if progress_cb:
                progress_cb(90, "Extracting archive...")

            root = os.path.abspath(extract_to)
            with tarfile.open(tmp_path, "r:*") as archive:
                for member in archive.getmembers():
                    destination = os.path.abspath(os.path.join(root, member.name))
                    if os.path.commonpath((root, destination)) != root:
                        raise RuntimeError("Downloaded archive contains an unsafe path.")
                archive.extractall(extract_to)
            if progress_cb:
                progress_cb(100, "Extraction complete.")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    @staticmethod
    def _flatten_resource_file(target_dir: str, filename: str) -> str:
        """Move a named file from a nested archive layout to its target root."""
        destination = os.path.join(target_dir, filename)
        if os.path.isfile(destination):
            return destination
        for source in Path(target_dir).rglob(filename):
            if source.is_file() and os.path.normcase(str(source)) != os.path.normcase(destination):
                os.makedirs(os.path.dirname(destination), exist_ok=True)
                if os.path.exists(destination):
                    os.remove(destination)
                shutil.move(str(source), destination)
                return destination
        return destination

    def _download_file(self, file_url: str, destination: str, progress_cb=None, label: str = "Downloading file...") -> str:
        """Download one resource atomically so interrupted files are ignored."""
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        temporary = f"{destination}.part"
        try:
            if progress_cb:
                progress_cb(-1, label)

            def _report_progress(block_num, block_size, total_size):
                if progress_cb and total_size and total_size > 0:
                    downloaded = block_num * block_size
                    percent = min(99, int((downloaded / total_size) * 100))
                    progress_cb(percent, f"{label} ({percent}%)")

            urllib.request.urlretrieve(file_url, temporary, reporthook=_report_progress)
            os.replace(temporary, destination)
            if progress_cb:
                progress_cb(100, f"{label} (100%)")
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
        return destination

    def _download_piper_new_pack(self, progress_cb=None) -> None:
        """Download the shared-config Vietnamese Piper pack.

        ``piper-new`` is a Hugging Face directory rather than a zip archive:
        it contains one config.json, one voices.json manifest, and one ONNX
        file per voice.  Download only missing files so clicking Download on
        an already populated install is fast and resumable.
        """
        target_dir = join_root("models", "piper")
        os.makedirs(target_dir, exist_ok=True)
        base_url = self._hf_blob_url("piper-new")
        manifest_path = os.path.join(target_dir, "voices.json")
        config_path = os.path.join(target_dir, "config.json")

        def _download_if_needed(filename: str, label: str, index: int, total: int) -> str:
            destination = os.path.join(target_dir, filename)
            if os.path.isfile(destination) and os.path.getsize(destination) > 0:
                if progress_cb:
                    progress_cb(int(index * 100 / max(total, 1)), f"Already available: {filename}")
                return destination

            def _scaled_progress(raw_percent, message):
                if not progress_cb:
                    return
                if raw_percent is None or raw_percent < 0:
                    percent = int(index * 100 / max(total, 1))
                else:
                    percent = int(((index + min(raw_percent, 100) / 100.0) * 100) / max(total, 1))
                progress_cb(min(99, percent), message)

            return self._download_file(
                f"{base_url}/{filename}",
                destination,
                _scaled_progress,
                label=label,
            )

        # Fetch the small manifest first; it is the source of truth for the
        # models shipped by piper-new and avoids hardcoding voice filenames.
        _download_if_needed("voices.json", "Downloading Piper voice manifest...", 0, 1)
        try:
            with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                manifest = json.load(handle)
        except Exception as exc:
            # A stale/corrupt local manifest should not make the whole pack
            # unusable; redownload it once and report a useful error if it is
            # still invalid.
            try:
                os.remove(manifest_path)
            except OSError:
                pass
            self._download_file(
                f"{base_url}/voices.json",
                manifest_path,
                progress_cb,
                label="Downloading Piper voice manifest...",
            )
            try:
                with open(manifest_path, "r", encoding="utf-8-sig") as handle:
                    manifest = json.load(handle)
            except Exception as retry_exc:
                raise RuntimeError(f"Piper voice manifest is invalid: {retry_exc}") from exc

        model_names: list[str] = []
        if isinstance(manifest, list):
            for item in manifest:
                if not isinstance(item, dict):
                    continue
                audio_path = str(item.get("audio_path", "")).replace("\\", "/").strip()
                filename = os.path.basename(audio_path)
                if filename.lower().endswith(".onnx") and filename not in model_names:
                    model_names.append(filename)
        if not model_names:
            raise RuntimeError("Piper voice manifest does not contain any ONNX models.")

        total = len(model_names) + 2  # config + manifest + models
        # Keep a valid config in the same target folder as all models.
        _download_if_needed("config.json", "Downloading shared Piper config...", 1, total)
        for offset, filename in enumerate(model_names, start=2):
            _download_if_needed(filename, f"Downloading Piper voice: {filename}", offset, total)

        usable = [
            name for name in model_names
            if os.path.isfile(os.path.join(target_dir, name))
            and os.path.getsize(os.path.join(target_dir, name)) > 0
        ]
        if not os.path.isfile(config_path) or os.path.getsize(config_path) <= 0 or not usable:
            raise RuntimeError(f"Piper download incomplete. Check {target_dir} and try again.")
        if progress_cb:
            progress_cb(100, f"Piper voices ready ({len(usable)} voices available).")

    def _download_vieneu_models(self, progress_cb=None) -> None:
        """Download VieNeu-TTS v3 Turbo ONNX models and MOSS audio tokenizer from Hugging Face."""
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import tqdm as hf_tqdm
        except ImportError as exc:
            raise ImportError(
                "huggingface_hub is not installed. Run `pip install huggingface_hub` first."
            ) from exc

        target_dir = join_root("models", "vieneu")
        os.makedirs(target_dir, exist_ok=True)
        target_hub = os.path.join(target_dir, "hub")
        os.makedirs(target_hub, exist_ok=True)
        os.environ["HF_HOME"] = target_dir

        def _make_progress_tqdm(start_pct: int, end_pct: int, label: str):
            class _Tqdm(hf_tqdm):
                def update(self, n=1):
                    super().update(n)
                    if progress_cb and self.total and self.total > 0:
                        raw_pct = min(99, max(0, int((self.n / self.total) * 100)))
                        scaled = start_pct + int((end_pct - start_pct) * raw_pct / 100)
                        progress_cb(scaled, f"{label} ({scaled}%)")
            return _Tqdm

        if progress_cb:
            progress_cb(0, "Downloading audio tokenizer...")

        # 1. Download audio tokenizer (~90 MB)
        snapshot_download(
            repo_id="OpenMOSS-Team/MOSS-Audio-Tokenizer-Nano-ONNX",
            cache_dir=target_hub,
            tqdm_class=_make_progress_tqdm(0, 25, "Downloading audio tokenizer..."),
        )
        if progress_cb:
            progress_cb(25, "Downloading VieNeu-TTS v3 Turbo ONNX model...")

        # 2. Download VieNeu-TTS v3 Turbo ONNX (~550 MB, excluding redundant PyTorch weights)
        allow_patterns = [
            "*.json",
            "*.onnx",
            "*.data",
            "*.npz",
            "*.txt",
            "onnx_update/*",
            "onnx_int8/*",
        ]
        snapshot_download(
            repo_id="pnnbao-ump/VieNeu-TTS-v3-Turbo",
            cache_dir=target_hub,
            allow_patterns=allow_patterns,
            tqdm_class=_make_progress_tqdm(25, 100, "Downloading VieNeu-TTS ONNX model..."),
        )

        if not self.is_resource_installed("voice:vieneu"):
            raise RuntimeError(
                f"VieNeu download completed, but model files were not found in {target_hub}."
            )
        if progress_cb:
            progress_cb(100, "VieNeu-TTS models are ready.")

    def download_resource(self, resource_id: str, progress_cb=None) -> None:
        if resource_id.startswith("whisper:"):
            model_name = resource_id.split(":", 1)[1].strip().lower()
            zip_name = self.WHISPER_ZIP_FILES.get(model_name)
            if not zip_name:
                raise ValueError(f"Unsupported Whisper model: {model_name}")
            zip_url = self._hf_blob_url(f"zipResource/{zip_name}")
            target_dir = join_root("models", "faster_whisper")
            self._download_and_extract_zip(zip_url, target_dir, progress_cb)
            if not self.is_resource_installed(resource_id):
                raise RuntimeError(
                    f"Whisper {model_name} download completed, but no model files were found in {target_dir}."
                )
            if progress_cb:
                progress_cb(100, f"Whisper {model_name} is ready.")
            return

        if resource_id == "sensevoice:model":
            zip_url = self._hf_blob_url("zipResource/sensevoice.zip")
            target_dir = os.path.join(self.workspace_root, "models", "sensevoice")
            self._download_and_extract_zip(zip_url, target_dir, progress_cb)
            # Support both archive layouts used by older resource packs:
            # files directly in sensevoice/ and files nested below a folder.
            for filename in ("model.int8.onnx", "tokens.txt"):
                destination = os.path.join(target_dir, filename)
                if os.path.isfile(destination):
                    continue
                for source in Path(target_dir).rglob(filename):
                    if source.is_file() and os.path.normcase(str(source)) != os.path.normcase(destination):
                        os.makedirs(os.path.dirname(destination), exist_ok=True)
                        if os.path.exists(destination):
                            os.remove(destination)
                        shutil.move(str(source), destination)
                        break
            if not self.is_resource_installed("sensevoice:model"):
                raise RuntimeError(
                    "SenseVoice download completed but model.int8.onnx and tokens.txt were not found in the target folder."
                )
            if progress_cb:
                progress_cb(100, "SenseVoice model is ready.")
            return

        if resource_id == "ocr:engine":
            target_dir = os.path.join(self.workspace_root, "rapidocr", "models")
            os.makedirs(target_dir, exist_ok=True)
            total = len(self._OCR_REQUIRED_MODELS)
            for index, filename in enumerate(self._OCR_REQUIRED_MODELS):
                url = self._hf_blob_url(f"rapidocr/models/{filename}")
                destination = os.path.join(target_dir, filename)
                temporary = f"{destination}.part"
                if progress_cb:
                    progress_cb(int(index * 100 / total), f"Downloading RapidOCR model: {filename}")
                try:
                    urllib.request.urlretrieve(url, temporary)
                    os.replace(temporary, destination)
                finally:
                    if os.path.exists(temporary):
                        os.remove(temporary)
            if not self.is_resource_installed("ocr:engine"):
                raise RuntimeError("RapidOCR download completed but one or more model files are missing.")
            if progress_cb:
                progress_cb(100, "RapidOCR models are ready.")
            return

        if resource_id == "diarization:segmentation":
            archive_url = (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
            )
            target_dir = join_root("models", "pyannote")
            self._download_and_extract_tar(archive_url, target_dir, progress_cb)
            self._flatten_resource_file(target_dir, "model.int8.onnx")
            if not self.is_resource_installed(resource_id):
                raise RuntimeError(
                    "Speaker segmentation download completed, but model.int8.onnx was not found."
                )
            if progress_cb:
                progress_cb(100, "Speaker segmentation model is ready.")
            return

        if resource_id == "diarization:embedding":
            file_url = (
                "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
                "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
            )
            target_dir = join_root("models", "pyannote")
            destination = os.path.join(
                target_dir,
                "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
            )
            self._download_file(file_url, destination, progress_cb, "Downloading speaker embedding model...")
            if not self.is_resource_installed(resource_id):
                raise RuntimeError("Speaker embedding download completed, but the ONNX model was not found.")
            if progress_cb:
                progress_cb(100, "Speaker embedding model is ready.")
            return

        if resource_id == "cuda:whisper":
            zip_url = self._hf_blob_url("zipResource/cuda12_fw_new.zip")
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
                zip_url = self._hf_blob_url("zipResource/piper-new.zip")
                target_dir = join_root("models")
                try:
                    self._download_and_extract_zip(zip_url, target_dir, progress_cb)
                except Exception as zip_exc:
                    print(f"[Piper] Zip download failed ({zip_exc}), falling back to pack sync...")
                    self._download_piper_new_pack(progress_cb)
                return
            if resource_id == "voice:pack-en":
                zip_url = self._hf_blob_url("zipResource/piper-en.zip")
                target_dir = join_root("models", "piper-en")
                self._download_and_extract_zip(zip_url, target_dir, progress_cb)
                return
            if resource_id == "voice:vieneu":
                self._download_vieneu_models(progress_cb)
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

        if resource_id in {self.NORMAL_AI_RESOURCE_ID, self.HIGH_AI_RESOURCE_ID}:
            raise ValueError(
                f"Auto download is not supported for '{resource_id}'. Use 'Open Download Page' to get the file manually."
            )

        raise ValueError(f"Unsupported resource: {resource_id}")
