from __future__ import annotations

import os


LOCAL_PROFILE = "local"
REMOTE_PROFILE = "remote"


def current_runtime_profile() -> str:
    profile = str(os.getenv("CAPCAP_RUNTIME_PROFILE", LOCAL_PROFILE) or LOCAL_PROFILE).strip().lower()
    if profile == REMOTE_PROFILE:
        return REMOTE_PROFILE
    return LOCAL_PROFILE


def is_remote_profile() -> bool:
    return current_runtime_profile() == REMOTE_PROFILE


def is_local_profile() -> bool:
    return current_runtime_profile() == LOCAL_PROFILE
