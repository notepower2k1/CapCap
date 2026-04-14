# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(r"D:\CodingTime\CapCap")
ui_root = project_root / "ui"
app_root = project_root / "app"

datas = [
    (str(project_root / "assets"), "assets"),
    (str(project_root / "bin" / "ffmpeg"), "bin/ffmpeg"),
    (str(project_root / "bin" / "mpv"), "bin/mpv"),
    (str(project_root / "app" / "voice_preview_catalog.release.json"), "app/voice_preview_catalog.json"),
    (str(project_root / ".env_example"), "."),
]

hiddenimports = [
    "main_window",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "mpv",
    "remote_api",
    "engines.remote_whisper_adapter",
    "engines.remote_translator_adapter",
    "engines.remote_tts_adapter",
]


a = Analysis(
    [str(ui_root / "gui_remote.py")],
    pathex=[str(project_root), str(ui_root), str(app_root)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "llama_cpp",
        "faster_whisper",
        "demucs",
        "piper",
        "piper.config",
        "edge_tts",
        "vietnormalizer.normalizer",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CapCapRemote",
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
    name="CapCapRemote",
)
