import os
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
        os.path.join(bundle_root(), "assets", *parts),
        join_root("assets", *parts),
    )


def app_path(*parts: str) -> str:
    return first_existing_path(
        os.path.join(bundle_root(), "app", *parts),
        join_root("app", *parts),
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
        os.path.join(bundle_root(), "models", *parts),
        join_root("models", *parts),
    )


def temp_path(*parts: str) -> str:
    return join_root("temp", *parts)


def output_path(*parts: str) -> str:
    return join_root("output", *parts)
