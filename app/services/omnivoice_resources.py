"""Pinned, optional audio.cpp binaries and original OmniVoice GGUF resources."""
from pathlib import Path
import os
import tempfile
import zipfile
import hashlib

from engines.omnivoice_tts import MODEL_FILE, MODEL_URL, MODEL_SIZE, MODEL_SHA256, RUNTIME_VERSION, CUDA_LIBRARIES, unload_omnivoice

MODEL_ID = "voice:omnivoice"
CPU_ID = "voice:omnivoice-runtime-cpu"
GPU_ID = "voice:omnivoice-runtime-vulkan"
CUDA_ID = "voice:omnivoice-runtime-cuda"
IDS = (MODEL_ID, CPU_ID, GPU_ID, CUDA_ID)


def _shared_cuda_dir(workspace):
    for name in ("cuda12_fw", "cuda12_fw_new"):
        directory = Path(workspace)/"bin"/name
        if all((directory/dll).is_file() and (directory/dll).stat().st_size > 0 for dll in CUDA_LIBRARIES):
            return directory
    return None


def _backend(resource_id):
    return {GPU_ID: "vulkan", CUDA_ID: "cuda"}.get(resource_id, "cpu")


def _target(workspace, resource_id):
    root = Path(workspace)
    if resource_id == MODEL_ID:
        return root / "models" / "omnivoice" / MODEL_FILE
    return root / "bin" / "audio_cpp" / _backend(resource_id) / "audiocpp_server.exe"


def installed(workspace, resource_id):
    path = _target(workspace, resource_id)
    try:
        if resource_id == CUDA_ID:
            if not (path.parent/"ggml-cuda.dll").is_file():
                return False
            if not _shared_cuda_dir(workspace) and not all((path.parent/name).is_file() for name in CUDA_LIBRARIES):
                return False
        with path.open("rb") as handle:
            return handle.read(4) == b"GGUF" and path.stat().st_size == MODEL_SIZE if resource_id == MODEL_ID else handle.read(2) == b"MZ"
    except OSError:
        return False


def entries(workspace):
    result = []
    for resource_id, name, description in (
        (MODEL_ID, "OmniVoice original (GGUF Q8, 1.3 GB)", "Original multilingual OmniVoice model for offline speech and voice cloning. Requires an audio.cpp runtime; CUDA is recommended for NVIDIA GPUs."),
        (CUDA_ID, "OmniVoice audio.cpp CUDA GPU runtime", "Recommended for NVIDIA GPUs. Downloads audio.cpp (444 MB), reuses a complete GPU Acceleration Pack, or downloads CUDA libraries (579 MB) if needed. GPU mode prefers CUDA."),
        (CPU_ID, "OmniVoice audio.cpp CPU runtime (29 MB)", "Offline CPU runtime. No Python/PyTorch or CUDA installation required."),
        (GPU_ID, "OmniVoice audio.cpp Vulkan GPU runtime (78 MB)", "Optional GPU runtime using Vulkan. Requires a compatible graphics driver; used when GPU mode is selected."),
    ):
        target = _target(workspace, resource_id)
        backend = _backend(resource_id)
        suffix = "cuda12.4" if backend == "cuda" else f"{backend}-portable"
        url = MODEL_URL if resource_id == MODEL_ID else f"https://github.com/0xShug0/audio.cpp/releases/download/{RUNTIME_VERSION}/audio-{RUNTIME_VERSION}-bin-windows-x64-{suffix}.zip"
        result.append(dict(id=resource_id, name=name, kind="voice", status="installed" if installed(workspace, resource_id) else "missing",
                           target_dir=str(target.parent), expected_filename=target.name, download_url=url,
                           auto_download_supported=True, description=description))
    return result


def download(service, resource_id, progress_cb=None):
    entry = next(e for e in entries(service.workspace_root) if e["id"] == resource_id)
    target = _target(service.workspace_root, resource_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    unload_omnivoice()
    # Stage on the same volume; interrupted downloads never replace installed files.
    staging_parent = target.parent if resource_id == MODEL_ID else target.parent.parent
    with tempfile.TemporaryDirectory(prefix=".omnivoice-install-", dir=staging_parent) as folder:
        folder = Path(folder)
        archive = folder / (MODEL_FILE if resource_id == MODEL_ID else "runtime.zip")
        def stage_progress(start, span):
            return (lambda percent, message: progress_cb(start + min(100, max(0, percent))*span//100, message)) if progress_cb else None
        primary_progress = stage_progress(0, 40) if resource_id == CUDA_ID else progress_cb
        service._download_file(entry["download_url"], str(archive), primary_progress, entry["name"])
        if resource_id == MODEL_ID:
            with archive.open("rb") as handle:
                if handle.read(4) != b"GGUF" or archive.stat().st_size != MODEL_SIZE:
                    raise RuntimeError("Invalid OmniVoice GGUF download.")
                handle.seek(0)
                if hashlib.file_digest(handle, "sha256").hexdigest() != MODEL_SHA256:
                    raise RuntimeError("OmniVoice model checksum mismatch. Please download it again.")
            os.replace(archive, target)
        else:
            staged = folder / "unpacked"
            staged.mkdir()
            archives = [archive]
            shared_cuda = _shared_cuda_dir(service.workspace_root) if resource_id == CUDA_ID else None
            if resource_id == CUDA_ID and not shared_cuda:
                dependency = folder / "cuda-libraries.zip"
                url = f"https://github.com/0xShug0/audio.cpp/releases/download/{RUNTIME_VERSION}/audio-{RUNTIME_VERSION}-cudart-windows-x64-cuda12.4.zip"
                service._download_file(url, str(dependency), stage_progress(40, 50), "OmniVoice CUDA libraries")
                archives.append(dependency)
            for source in archives:
                with zipfile.ZipFile(source) as package:
                    for member in package.infolist():
                        path = (staged / member.filename).resolve()
                        if not path.is_relative_to(staged.resolve()) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                            raise RuntimeError("Unsafe audio.cpp archive entry.")
                    package.extractall(staged)
            binary = staged / "audiocpp_server.exe"
            if not binary.is_file():
                raise RuntimeError("audio.cpp server is missing from the runtime archive.")
            with binary.open("rb") as handle:
                if handle.read(2) != b"MZ":
                    raise RuntimeError("Invalid audio.cpp executable.")
            if resource_id == CUDA_ID and (not (staged/"ggml-cuda.dll").is_file() or (not shared_cuda and not all((staged/name).is_file() for name in CUDA_LIBRARIES))):
                raise RuntimeError("The audio.cpp CUDA runtime libraries are incomplete.")
            # Keep accompanying DLLs and model specs, not only the executable.
            destination = target.parent.resolve()
            if not destination.is_relative_to(Path(service.workspace_root).resolve()):
                raise RuntimeError("Runtime destination must be inside the workspace.")
            backup = folder / "previous"
            destination.rename(backup)
            try:
                staged.rename(destination)
            except OSError:
                backup.rename(destination)
                raise
    if not installed(service.workspace_root, resource_id):
        raise RuntimeError("OmniVoice resource installation could not be verified.")
    if progress_cb:
        progress_cb(100, "OmniVoice resource is ready.")
