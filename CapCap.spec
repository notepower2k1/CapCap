# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(r"D:\CodingTime\CapCap")
ui_root = project_root / "ui"
app_root = project_root / "app"
python_site_packages = Path(r"C:\Users\Thach\AppData\Local\Programs\Python\Python311\Lib\site-packages")
piper_pkg_root = python_site_packages / "piper"
faster_whisper_pkg_root = python_site_packages / "faster_whisper"

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "bin" / "ffmpeg"), "bin/ffmpeg"),
    (str(project_root / "bin" / "mpv"), "bin/mpv"),
    (str(project_root / "app" / "voice_preview_catalog.json"), "app"),
    (str(project_root / ".env_example"), "."),
]

if (project_root / "models" / "piper").exists():
    datas.append((str(project_root / "models" / "piper"), "models/piper"))

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
