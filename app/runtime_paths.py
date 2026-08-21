import os
import subprocess
import sys
from pathlib import Path


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


def bin_path(*parts: str) -> str:
    primary = os.path.join(bundle_root(), "bin", *parts)
    workspace_fallback = join_root("bin", *parts)
    cwd_fallback = os.path.join(os.getcwd(), "bin", *parts)
    exe_fallback = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "bin", *parts)
    return first_existing_path(primary, workspace_fallback, cwd_fallback, exe_fallback)


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


def subprocess_text_kwargs() -> dict:
    """Return safe options for text captured from external Windows tools.

    ``subprocess`` otherwise decodes captured output using the current Windows
    ANSI code page (often reported by Python as ``charmap``).  FFmpeg and
    FFprobe include the input path in diagnostics, so a Unicode file or user
    path can make that implicit decode fail before the actual workflow starts.
    Our bundled tools emit UTF-8 diagnostics; replacement is intentional for
    diagnostic text so an unexpected third-party byte never aborts a job.
    """
    return {
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        **subprocess_hidden_kwargs(),
    }
