import os
import shutil
import subprocess
import sys
from pathlib import Path


IS_WINDOWS = os.name == "nt"


def bundle_root() -> str:
    meipass = getattr(sys, "_MEIPASS", "")
    if meipass:
        return os.path.abspath(str(meipass))
    if getattr(sys, "frozen", False):
        internal_dir = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "_internal")
        if os.path.isdir(internal_dir):
            return internal_dir
    return str(Path(__file__).resolve().parents[1])


def workspace_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return str(Path(__file__).resolve().parents[1])


def join_root(*parts: str) -> str:
    return os.path.join(workspace_root(), *parts)


def asset_path(*parts: str) -> str:
    return first_existing_path(
        join_root("assets", *parts),
        os.path.join(bundle_root(), "assets", *parts),
    )


def app_path(*parts: str) -> str:
    return first_existing_path(
        join_root("app", *parts),
        os.path.join(bundle_root(), "app", *parts),
    )


def first_existing_path(*candidates: str) -> str:
    for candidate in candidates:
        path = str(candidate or "").strip()
        if path and os.path.exists(path):
            return path
    return str(candidates[0] if candidates else "")


def _bin_roots() -> list:
    return [
        os.path.join(bundle_root(), "bin"),
        join_root("bin"),
        os.path.join(os.getcwd(), "bin"),
        os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "bin"),
    ]


def _part_variants(part: str) -> list:
    """Return the acceptable spellings of a bundled file name on this OS.

    The repository ships Windows binaries (``ffmpeg.exe``), so call sites name
    them with the ``.exe`` suffix.  On Linux and macOS the same tool has no
    extension, and a bundled ``.exe`` is a PE image that cannot be executed, so
    the suffixed spelling must never be accepted there.
    """
    name = str(part or "")
    if IS_WINDOWS or not name.lower().endswith(".exe"):
        return [name]
    return [name[: -len(".exe")]]


def bin_path(*parts: str) -> str:
    if parts:
        tail_variants = _part_variants(parts[-1])
        head = list(parts[:-1])
    else:
        tail_variants = []
        head = []

    candidates = []
    for root in _bin_roots():
        if tail_variants:
            for variant in tail_variants:
                candidates.append(os.path.join(root, *head, variant))
        else:
            candidates.append(root)
    return first_existing_path(*candidates)


def tool_path(name: str, *, subdir: str = "ffmpeg") -> str:
    """Resolve a command-line tool such as ``ffmpeg`` or ``ffprobe``.

    Bundled copies win so a packaged build stays self-contained.  When nothing
    is bundled — the normal situation on Linux and macOS, where the ``.exe``
    binaries in ``bin/`` are unusable — fall back to the copy on ``PATH`` and
    finally to the bare name, which yields a readable "not found" error from
    ``subprocess`` instead of a misleading missing-file path.
    """
    base = str(name or "").strip()
    if not base:
        return ""
    filename = base + ".exe" if IS_WINDOWS else base

    for candidate in (bin_path(subdir, filename), bin_path(filename)):
        if candidate and os.path.isfile(candidate):
            return candidate

    found = shutil.which(base)
    if found:
        return found
    return base


def ffmpeg_path() -> str:
    return tool_path("ffmpeg")


def ffprobe_path() -> str:
    return tool_path("ffprobe")


def mpv_library_path() -> str:
    """Locate the libmpv shared library that ships with the Windows bundle.

    Linux and macOS are expected to use the system libmpv (``libmpv.so.2`` /
    ``libmpv.2.dylib``), which ``python-mpv`` discovers on its own, so an empty
    string there means "let the loader find it".
    """
    if IS_WINDOWS:
        names = ("libmpv-2.dll", "mpv-2.dll")
    else:
        names = ("libmpv.so.2", "libmpv.so", "libmpv.2.dylib", "libmpv.dylib")
    for name in names:
        candidate = bin_path("mpv", name)
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def models_path(*parts: str) -> str:
    return first_existing_path(
        join_root("models", *parts),
        os.path.join(bundle_root(), "models", *parts),
    )


def temp_path(*parts: str) -> str:
    return join_root("temp", *parts)


def output_path(*parts: str) -> str:
    return join_root("output", *parts)


def subprocess_hidden_kwargs() -> dict:
    """Return Windows flags that keep console child processes invisible.

    The GUI build has no console of its own.  Without these flags, console
    programs such as FFmpeg/FFprobe create a temporary console window every
    time they start, which causes visible flashes and adds process-launch
    overhead.  The debug console build remains unaffected.
    """
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
