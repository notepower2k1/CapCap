from __future__ import annotations

import os

import requests


DEFAULT_REMOTE_API_URL = "http://127.0.0.1:8765"


def remote_api_base_url() -> str:
    return str(os.getenv("CAPCAP_REMOTE_API_URL", DEFAULT_REMOTE_API_URL) or DEFAULT_REMOTE_API_URL).strip().rstrip("/")


def remote_api_token() -> str:
    return str(os.getenv("CAPCAP_REMOTE_API_TOKEN", "") or "").strip()


def remote_api_timeout_seconds() -> int:
    raw = str(os.getenv("CAPCAP_REMOTE_API_TIMEOUT", "600") or "600").strip()
    try:
        return max(15, int(raw))
    except Exception:
        return 600


def remote_api_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = remote_api_token()
    if token:
        headers["X-CapCap-Token"] = token
    return headers


def remote_api_post(path: str, payload: dict, *, timeout: int | None = None) -> dict:
    url = f"{remote_api_base_url()}{path}"
    response = requests.post(
        url,
        json=payload,
        headers=remote_api_headers(),
        timeout=timeout or remote_api_timeout_seconds(),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Remote API returned an invalid response.")
    if not data.get("ok", False):
        raise RuntimeError(str(data.get("error") or "Remote API request failed."))
    return data


def remote_api_get(path: str, *, timeout: int | None = None) -> dict:
    url = f"{remote_api_base_url()}{path}"
    response = requests.get(url, headers=remote_api_headers(), timeout=timeout or 10)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Remote API returned an invalid response.")
    return data
