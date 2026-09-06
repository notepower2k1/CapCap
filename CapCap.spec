# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os
import glob as _glob
import faster_whisper
import rapidocr
import vieneu
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_root = Path(r"D:\CodingTime\CapCap")
ui_root = project_root / "ui"
app_root = project_root / "app"

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "bin" / "ffmpeg"), "bin/ffmpeg"),
    (str(project_root / "bin" / "mpv"), "bin/mpv"),
    (str(project_root / "bin" / "UVR-MDX-NET-Inst_HQ_3.onnx"), "bin"),
    (str(project_root / "bin" / "silero_vad.onnx"), "bin"),
    (str(project_root / "app" / "voice_preview_catalog.json"), "app"),
    (str(project_root / "app" / "voice_download_catalog.json"), "app"),
    (str(project_root / "app" / "voice_preview_catalog.release.json"), "app"),
    (str(project_root / "app" / "translation" / "prompts"), "app/translation/prompts"),
    (str(project_root / "models" / "piper" / "ngochuyen.onnx"), "models/piper"),
    # piper-new uses one shared config/voice manifest for all Vietnamese
    # models; PiperVoice receives this config explicitly at runtime.
    (str(project_root / "models" / "piper" / "config.json"), "models/piper"),
    (str(project_root / "models" / "piper" / "voices.json"), "models/piper"),
    (str(project_root / "models" / "pyannote" / "model.int8.onnx"), "models/pyannote"),
    (str(project_root / "models" / "pyannote" / "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"), "models/pyannote"),
    (str(project_root / "models" / "pyannote" / "LICENSE"), "models/pyannote"),
    # Ship one ready-to-use offline transcription engine. Keep only the
    # quantized model and vocabulary; test audio, caches, and export docs stay
    # out of the installer and Whisper/OCR remain optional downloads.
    (str(project_root / "models" / "sensevoice" / "model.int8.onnx"), "models/sensevoice"),
    (str(project_root / "models" / "sensevoice" / "tokens.txt"), "models/sensevoice"),
    (str(project_root / "bin" / "cuda12_fw" / "README.txt"), "bin/cuda12_fw"),
    (str(project_root / "models" / "faster_whisper" / "README.txt"), "models/faster_whisper"),
    (str(project_root / "app" / "utils" / "voice_preview_utils.py"), "utils"),
    (str(project_root / ".env_example"), "."),
    (os.path.join(os.path.dirname(faster_whisper.__file__), "assets"), "faster_whisper/assets"),
    (os.path.join(os.path.dirname(rapidocr.__file__), "models"), "rapidocr/models"),
    # RapidOCR loads these YAML files at runtime before it resolves the ONNX
    # models.  They are data files, so PyInstaller does not always discover
    # them from imports alone.
    (os.path.join(os.path.dirname(rapidocr.__file__), "config.yaml"), "rapidocr"),
    (os.path.join(os.path.dirname(rapidocr.__file__), "default_models.yaml"), "rapidocr"),
    (os.path.join(os.path.dirname(vieneu.__file__), "assets"), "vieneu/assets"),
    (str(project_root / "models" / "vieneu"), "models/vieneu"),
    (str(project_root / "ui" / "views" / "editor"), "views/editor"),
]
datas += collect_data_files("piper")
# The normalizer package ships its built-in CSV rules as package data.  Keep
# them in the frozen build; project dictionaries are stored separately in
# each project's state.json and are not bundled.
datas += collect_data_files("vietnormalizer")
datas += collect_data_files("vieneu")
datas += collect_data_files("vieneu_utils")

# RapidOCR selects its ONNX implementation through runtime configuration.
# Listing only ``rapidocr`` misses these dynamically imported modules in a
# frozen worker and can make a bundled OCR install look intermittently
# unavailable. Collect the package's submodules, while leaving the unused
# Torch backend excluded by the existing package exclusions below.
rapidocr_hiddenimports = collect_submodules("rapidocr")
vieneu_hiddenimports = collect_submodules("vieneu") + collect_submodules("vieneu_utils")

# Exclude heavy packages we don't use
excludes = [
    "torch",
    "torchaudio",
    "torchvision",
    "demucs",
    # Local AI/llama.cpp was removed from the application. Keep it out of
    # packaged builds even if an installed dependency exposes it indirectly.
    "llama_cpp",
    "llama_cpp.*",
    "matplotlib",

    "comtypes",
    "phonenumbers",
    "uvicorn",
    "msgpack",
    "orjson",
    "safetensors",
    "xxhash",
    "brotli",
    "contourpy",
    "kiwisolver",
    "packaging",
    "cryptography",
    "psutil",
    "websockets",
]


a = Analysis(
    [str(ui_root / "gui.py")],
    pathex=[str(project_root), str(ui_root), str(app_root)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "gui",
        "main_window",
        "PySide6.QtSvg",
        "PySide6.QtSvgWidgets",
        "mpv",
        "remote_api",
        "remote_api_server",
        "services",
        "services.project_service",
        "services.gui_project_bridge",
        "services.voice_catalog_service",
        "services.resource_download_service",
        "services.engine_runtime",
        "services.workflow_runtime",
        "services.segment_service",
        "services.chunking_service",
        "services.asr_merge_service",
        "services.segment_regroup_service",
        # SpeakerDiarizationService is resolved lazily through services.__getattr__.
        # Include its concrete module so the frozen worker can import it.
        "services.speaker_diarization_service",
        # Dynamically loaded adapters (EngineRuntime uses importlib)
        "engines.ffmpeg_adapter",
        "engines.preview_adapter",
        "engines.subtitle_adapter",
        "engines.audio_mix_adapter",
        "engines.whisper_adapter",
        "engines.sensevoice_adapter",
        "engines.translator_adapter",
        "engines.tts_adapter",
        "engines.demucs_adapter",
        # CapCut online API (STT & TTS)
        "app.capcut",
        "app.capcut.signing",
        "app.capcut.device",
        "app.capcut.stt",
        "app.capcut.tts",
        "capcut",
        "capcut.signing",
        "capcut.device",
        "capcut.stt",
        "capcut.tts",
        # Lazy-loaded translation providers
        "translation.providers.gemini_polisher",
        "translation.providers.google_web_translator",
        "translation.providers.microsoft_translator",
        # Lazy-loaded workflows
        "workflows.prepare_workflow",
        "workflows.voice_workflow",
        "workflows.export_workflow",
        # Required for voice workflow
        "utils.voice_preview_utils",
        # Required for timeline + audio
        "numpy",
        "pydub",
        "scipy",
        "librosa",
        "soundfile",
        "onnxruntime",
        "openai",
        "audio_mixer",
        "vocal_processor",
        "whisper_processor",
        "faster_whisper",
        "ctranslate2",
        "edge_tts",
        "dotenv",
        "huggingface_hub",
        "tts_processor",
        "vieneu_tts",
        "app.vieneu_tts",
        "ui.widgets.voice_clone_dialog",
        "tokenizers",
        "kaldi_native_fbank",
        "soxr",
        "vietnormalizer",
        "vietnormalizer.normalizer",
        "vietnormalizer.processor",
        "vietnormalizer.transliterator",
        "vietnormalizer.detector",
        "vietnormalizer.data",
        "preview_processor",
        "video_processor",
        "subtitle_builder",
        "runtime_paths",
        # Explicitly include the namespace-package module imported by the
        # timeline at runtime.  PyInstaller does not reliably discover
        # modules beneath the project-level ``app`` namespace automatically.
        "app.runtime_paths",
        "runtime_profile",
        # OCR engine
        "rapidocr",
        "sherpa_onnx",
        "shapely",
        "cv2",
        "omegaconf",
        "pyclipper",
    ] + rapidocr_hiddenimports + vieneu_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

# Force-exclude heavy packages from final bundle
_EXCLUDE_PREFIXES = ("torch", "torchaudio", "torchvision", "demucs")
a.pure = [m for m in a.pure if not str(m[0]).replace("\\", "/").split("/")[0].split(".")[0] in _EXCLUDE_PREFIXES]
a.binaries = [m for m in a.binaries if not str(m[0]).replace("\\", "/").split("/")[0].split(".")[0] in _EXCLUDE_PREFIXES]
# PyInstaller can collect the same ONNX Runtime CUDA provider under a nested
# invalid destination as well as the normal capi directory. Keep only the
# normal provider copy; CUDA runtime DLLs are installed on demand by the
# Resource Manager, not bundled into the application.
a.binaries = [m for m in a.binaries if str(m[0]).replace("\\", "/") != "onnxruntime/capi/onnxruntime/capi/onnxruntime_providers_cuda.dll"]

# Do not silently turn the installer into a CUDA runtime installer. Native
# dependency analysis of CTranslate2 can discover these DLLs from the CUDA
# Toolkit installed on the build machine and place duplicate copies in
# ``_internal``. GPU mode intentionally resolves them from the separately
# downloadable ``bin/cuda12_fw`` resource directory instead. Keeping this
# exclusion explicit makes CPU-only installs small and keeps the Resource
# Manager as the single source for the GPU runtime.
_CUDA_RUNTIME_DLL_NAMES = {
    "cublas64_12.dll",
    "cublaslt64_12.dll",
    "cudart64_12.dll",
    "cufft64_11.dll",
}
a.binaries = [
    m for m in a.binaries
    if Path(str(m[0])).name.lower() not in _CUDA_RUNTIME_DLL_NAMES
]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CapCap",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_dir=str(project_root / "upx"),
    # Keep the console build as the debugging/default configuration.  The
    # windowed production spec sets CAPCAP_WINDOWED_BUILD=1 before executing
    # this file.
    console=os.environ.get("CAPCAP_WINDOWED_BUILD", "0").strip() != "1",
    icon=str(project_root / "assets" / "capcap.ico"),
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_dir=str(project_root / "upx"),
    upx_exclude=["onnxruntime*", "ffmpeg*", "ffprobe*", "mpv*", "cudnn*", "cublas*", "cudart*", "libomp*"],
    name="CapCap",
)
