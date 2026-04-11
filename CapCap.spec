# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(r"D:\CodingTime\CapCap")
ui_root = project_root / "ui"
app_root = project_root / "app"
python_site_packages = Path(r"C:\Users\Thach\AppData\Local\Programs\Python\Python311\Lib\site-packages")
piper_pkg_root = python_site_packages / "piper"
faster_whisper_pkg_root = python_site_packages / "faster_whisper"
default_piper_voice = "vi_VN-vais1000-medium"
piper_models_dir = project_root / "models" / "piper"

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "bin" / "ffmpeg"), "bin/ffmpeg"),
    (str(project_root / "bin" / "mpv"), "bin/mpv"),
    (str(project_root / "app" / "voice_preview_catalog.release.json"), "app/voice_preview_catalog.json"),
    (str(project_root / ".env_example"), "."),
]

if piper_models_dir.exists():
    default_piper_model = piper_models_dir / f"{default_piper_voice}.onnx"
    default_piper_model_config = piper_models_dir / f"{default_piper_voice}.onnx.json"
    piper_voice_meta = piper_models_dir / "voices_meta.json"
    if default_piper_model.exists():
        datas.append((str(default_piper_model), f"models/piper/{default_piper_model.name}"))
    if default_piper_model_config.exists():
        datas.append((str(default_piper_model_config), f"models/piper/{default_piper_model_config.name}"))
    if piper_voice_meta.exists():
        datas.append((str(piper_voice_meta), f"models/piper/{piper_voice_meta.name}"))

if (piper_pkg_root / "espeak-ng-data").exists():
    datas.append((str(piper_pkg_root / "espeak-ng-data"), "piper/espeak-ng-data"))

if (piper_pkg_root / "tashkeel").exists():
    datas.append((str(piper_pkg_root / "tashkeel"), "piper/tashkeel"))

if (faster_whisper_pkg_root / "assets").exists():
    datas.append((str(faster_whisper_pkg_root / "assets"), "faster_whisper/assets"))

hiddenimports = [
    "main_window",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "mpv",
    "llama_cpp",
    "faster_whisper",
    "edge_tts",
    "piper",
    "piper.config",
    "vietnormalizer.normalizer",
]


a = Analysis(
    [str(ui_root / "gui.py")],
    pathex=[str(project_root), str(ui_root), str(app_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
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
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CapCap",
)
