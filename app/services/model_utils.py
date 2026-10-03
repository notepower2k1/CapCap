"""Shared model-matching utilities used by resource_download_service and whisper_processor."""
from __future__ import annotations


def matches_whisper_model_dir(
    dir_name: str = "",
    model_name: str = "",
    folder_name: str = "",
) -> bool:
    """Return True if *dir_name* (or *folder_name*) accurately corresponds to *model_name*.

    Prevents substring collisions such as 'large-v3' matching 'large-v3-turbo'.
    """
    effective_dir = folder_name or dir_name
    target = model_name

    # Auto-detect if caller passed (model_name, dir_name) positionally where target is a repo directory
    if target.startswith("models--") and not effective_dir.startswith("models--"):
        effective_dir, target = target, effective_dir

    dname = effective_dir.lower().strip()
    target = target.lower().strip()

    if dname == target:
        return True

    if dname.startswith("models--"):
        parts = dname.split("--")
        repo = parts[-1] if len(parts) >= 3 else dname[len("models--"):]
    else:
        repo = dname

    if target in ("turbo", "large-v3-turbo"):
        return "turbo" in repo

    if "turbo" in repo:
        return False

    if "distil" in target:
        base = target.replace("distil-", "")
        return "distil" in repo and (repo.endswith(f"-{target}") or base in repo)

    if "distil" in repo:
        return False

    return repo == target or repo.endswith(f"-{target}")
