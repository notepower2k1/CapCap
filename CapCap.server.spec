# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


project_root = Path(r"D:\CodingTime\CapCap")
app_root = project_root / "app"
python_site_packages = Path(r"C:\Users\Thach\AppData\Local\Programs\Python\Python311\Lib\site-packages")
faster_whisper_pkg_root = python_site_packages / "faster_whisper"

datas = [
    (str(project_root / ".env_example"), "."),
]

if (faster_whisper_pkg_root / "assets").exists():
    datas.append((str(faster_whisper_pkg_root / "assets"), "faster_whisper/assets"))

hiddenimports = [
    "faster_whisper",
    "llama_cpp",
    "requests",
]


a = Analysis(
    [str(app_root / "remote_api_server.py")],
    pathex=[str(project_root), str(app_root)],
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
    name="CapCapServer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="CapCapServer",
)
