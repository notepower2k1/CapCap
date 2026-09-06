"""CapCut Online Speech-to-Text (STT) Integration for CapCap."""

from __future__ import annotations

import binascii
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

import requests

from runtime_paths import bin_path, subprocess_hidden_kwargs
try:
    from app.capcut.device import DeviceConfig, DEFAULT_DEVICE
    from app.capcut.signing import (
        aws4_authorization,
        compact_json,
        make_sign_header,
        make_trace_id,
        make_x_ss_stub,
        utc_now_for_vod,
    )
except ImportError:
    from .device import DeviceConfig, DEFAULT_DEVICE
    from .signing import (
        aws4_authorization,
        compact_json,
        make_sign_header,
        make_trace_id,
        make_x_ss_stub,
        utc_now_for_vod,
    )

DIRECT_API_BASE = "https://lv-pc-api-sinfonlinec.ulikecam.com"
VOD_API_HOST = "vod.bytedanceapi.com"
VOD_SPACE = "lv-mac-recognition"
JIANYING_USER_AGENT = "Cronet/TTNetVersion:7a947855 2024-05-06 QuicVersion:4bf243e0 2023-04-17"
REFERENCE_USER_AGENT = "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03"


def _get_ffmpeg_path() -> str:
    path = bin_path("ffmpeg", "ffmpeg.exe")
    if path and os.path.exists(path):
        return path
    import shutil
    return shutil.which("ffmpeg") or "ffmpeg"


def _get_ffprobe_path() -> str:
    path = bin_path("ffmpeg", "ffprobe.exe")
    if path and os.path.exists(path):
        return path
    import shutil
    return shutil.which("ffprobe") or "ffprobe"


def _probe_duration_seconds(media_path: str) -> float:
    ffprobe = _get_ffprobe_path()
    try:
        cmd = [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, **subprocess_hidden_kwargs())
        val = float(result.stdout.strip())
        if val > 0:
            return val
    except Exception:
        pass
    return 0.0


def _build_stt_device() -> DeviceConfig:
    device = {
        "aid": "3704",
        "app_name": "JianyingPro",
        "appvr": "6.0.1",
        "version_name": "6.0.1",
        "version_code": "6.0.1",
        "channel": "jianyingpro_0",
        "device_platform": "windows",
        "device_type": "Windows",
        "device_brand": "American Megatrends Inc.",
        "os_version": "10.0.26200",
        "device_id": str(secrets.randbelow(10**16)).zfill(16),
        "iid": str(secrets.randbelow(10**16)).zfill(16),
        "tdid": str(secrets.randbelow(10**16)).zfill(16),
        "region": "VN",
        "loc": "VN",
        "lan": "vi-VN",
        "pf": "4",
    }
    return DeviceConfig.from_dict(device)


def _new_worker_identity(base_device: DeviceConfig) -> DeviceConfig:
    values = base_device.to_dict()
    for field in ("device_id", "iid", "tdid"):
        values[field] = str(secrets.randbelow(10**20)).zfill(20)
    return DeviceConfig.from_dict(values)


def _direct_upload_credentials(device: DeviceConfig, session: requests.Session) -> dict:
    device_dict = device.to_dict()
    user_agent = JIANYING_USER_AGENT if device_dict["app_name"] == "JianyingPro" else REFERENCE_USER_AGENT
    sign_body = compact_json({"biz": "pc-recognition"})
    query = {
        key: device_dict[key]
        for key in (
            "aid", "app_name", "channel", "device_brand", "device_id",
            "device_platform", "device_type", "iid", "os_version",
            "version_code", "version_name",
        )
    }
    sign_url = f"{DIRECT_API_BASE}/lv/v1/upload_sign?{urlencode(query)}"
    device_time = str(int(time.time()))
    sign_headers = {
        "content-type": "application/json",
        "app-sdk-version": device_dict["appvr"],
        "appvr": device_dict["appvr"],
        "device-time": device_time,
        "lan": device_dict["lan"],
        "loc": device_dict["loc"],
        "pf": device_dict["pf"],
        "sign-ver": "1",
        "tdid": device_dict["tdid"],
        "user-agent": user_agent,
        "x-ss-dp": device_dict["aid"],
        "x-ss-stub": make_x_ss_stub(sign_body),
        "x-tt-trace-id": make_trace_id(),
    }
    sign_headers["sign"] = make_sign_header(
        sign_url,
        device_dict["appvr"],
        device_time,
        device_dict["tdid"],
        device_dict["pf"],
    )
    resp = session.post(sign_url, headers=sign_headers, data=sign_body.encode("utf-8"), timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("ret")) not in {"0", "None"}:
        raise RuntimeError(f"CapCut upload_sign failed: {data.get('errmsg') or data.get('ret')}")
    credentials = dict(data.get("data") or {})
    required = ("access_key_id", "secret_access_key", "session_token")
    if any(not credentials.get(key) for key in required):
        raise RuntimeError("CapCut upload_sign did not return temporary credentials")
    return {
        **credentials,
        "domain": VOD_API_HOST,
        "space_name": VOD_SPACE,
    }


def _direct_vod_upload(device: DeviceConfig, session: requests.Session, audio_path: Path, credentials: dict) -> str:
    raw = audio_path.read_bytes()
    crc32 = f"{binascii.crc32(raw) & 0xFFFFFFFF:08x}"

    def vod_headers(method: str, url: str, body: bytes):
        amz_date, http_date = utc_now_for_vod()
        authorization = aws4_authorization(
            method,
            url,
            body,
            credentials["access_key_id"],
            credentials["secret_access_key"],
            credentials["session_token"],
            amz_date,
        )
        return {
            "Authorization": authorization,
            "Date": http_date,
            "User-Agent": f"BDFileUpload({int(time.time() * 1000)})",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": "31536000",
            "X-Amz-Security-Token": credentials["session_token"],
            "accept-encoding": "identity",
        }

    apply_url = f"https://{VOD_API_HOST}/top/v1?{urlencode({'Action': 'ApplyUploadInner', 'SpaceName': VOD_SPACE, 'UseQuic': 'false', 'Version': '2020-11-19', 'device_platform': 'win'})}"
    apply_resp = session.get(apply_url, headers=vod_headers("GET", apply_url, b""), timeout=60)
    apply_resp.raise_for_status()
    apply_data = apply_resp.json()
    node = apply_data["Result"]["InnerUploadAddress"]["UploadNodes"][0]
    store = node["StoreInfos"][0]
    store_uri = store["StoreUri"]
    upload_auth = store["Auth"]
    upload_host = node["UploadHost"]

    transfer_url = f"https://{upload_host}/upload/v1/{store_uri}?{urlencode({'uploadid': store['UploadID'], 'part_number': '0', 'phase': 'transfer'})}"
    transfer_headers = {
        "Authorization": upload_auth,
        "User-Agent": f"BDFileUpload({int(time.time() * 1000)})",
        "X-Upload-Content-CRC32": crc32,
        "accept-encoding": "identity",
    }
    transfer_resp = session.post(transfer_url, headers=transfer_headers, data=raw, timeout=300)
    transfer_resp.raise_for_status()

    finish_url = f"https://{upload_host}/upload/v1/{store_uri}?{urlencode({'uploadmode': 'part', 'phase': 'finish', 'uploadid': store['UploadID']})}"
    finish_body = f"0:{crc32}".encode("ascii")
    finish_headers = dict(transfer_headers)
    finish_headers.pop("X-Upload-Content-CRC32", None)
    finish_resp = session.post(finish_url, headers=finish_headers, data=finish_body, timeout=60)
    finish_resp.raise_for_status()
    return store_uri


def _direct_query(device: DeviceConfig, session: requests.Session, store_uri: str, duration_ms: int, timeout: float = 120.0, poll_interval: float = 1.0) -> list[dict]:
    device_dict = device.to_dict()
    user_agent = JIANYING_USER_AGENT if device_dict["app_name"] == "JianyingPro" else REFERENCE_USER_AGENT
    query = {
        key: device_dict[key]
        for key in (
            "aid", "app_name", "channel", "device_brand", "device_id",
            "device_platform", "iid", "os_version", "version_code", "version_name",
        )
    }
    headers_base = {
        "content-type": "application/json",
        "user-agent": user_agent,
        "accept-encoding": "gzip, deflate",
        "x-ss-dp": device_dict["aid"],
    }

    submit = {
        "adjust_endtime": 200,
        "audio": store_uri,
        "caption_type": 2,
        "client_request_id": str(uuid.uuid4()),
        "max_lines": 1,
        "songs_info": [{"end_time": int(duration_ms), "id": "", "start_time": 0}],
        "words_per_line": 16,
    }
    submit_text = compact_json(submit)
    submit_headers = dict(headers_base)
    submit_headers["x-ss-stub"] = make_x_ss_stub(submit_text)
    submit_url = f"{DIRECT_API_BASE}/lv/v1/audio_subtitle/submit?{urlencode(query)}"
    submit_resp = session.post(submit_url, headers=submit_headers, data=submit_text.encode("utf-8"), timeout=60)
    submit_resp.raise_for_status()
    submit_data = submit_resp.json()
    query_id = ((submit_data.get("data") or {}).get("id"))
    if not query_id:
        raise RuntimeError(f"CapCut audio_subtitle submit returned no id: ret={submit_data.get('ret')}")

    query_body = {"id": query_id, "pack_options": {"need_attribute": True}}
    query_text = compact_json(query_body)
    query_headers = dict(headers_base)
    query_headers["x-ss-stub"] = make_x_ss_stub(query_text)
    query_url = f"{DIRECT_API_BASE}/lv/v1/audio_subtitle/query?{urlencode(query)}"

    started = time.monotonic()
    while time.monotonic() - started < timeout:
        resp = session.post(query_url, headers=query_headers, data=query_text.encode("utf-8"), timeout=60)
        resp.raise_for_status()
        data = resp.json()
        utterances = ((data.get("data") or {}).get("utterances")) or []
        if utterances:
            return utterances
        if str(data.get("ret")) not in {"0", "None"}:
            raise RuntimeError(f"CapCut audio_subtitle query error: ret={data.get('ret')} msg={data.get('errmsg')}")
        time.sleep(poll_interval)
    raise TimeoutError(f"CapCut STT query timed out after {timeout} seconds")


def _extract_chunk_mp3(source_audio: str, output_path: str, start: float, duration: float) -> None:
    ffmpeg = _get_ffmpeg_path()
    cmd = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", str(source_audio),
        "-t", f"{duration:.3f}",
        "-vn", "-ac", "1", "-ar", "16000",
        "-codec:a", "libmp3lame", "-b:a", "32k",
        "-y", str(output_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True, **subprocess_hidden_kwargs())


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _merge_chunks(chunks: list[tuple[int, list[dict]]]) -> list[dict]:
    all_items = [item for _, items in chunks for item in items if str(item.get("text") or "").strip()]
    all_items.sort(key=lambda item: (int(item.get("start_time", 0)), int(item.get("end_time", 0))))
    merged = []
    for item in all_items:
        text = str(item.get("text") or "").strip()
        start = int(item.get("start_time", 0))
        end = int(item.get("end_time", start))
        duplicate = False
        for old in reversed(merged[-12:]):
            old_chunk = old.get("_chunk")
            item_chunk = item.get("_chunk")
            if old_chunk is not None and item_chunk is not None and old_chunk == item_chunk:
                continue
            old_start = int(old.get("start_time", 0))
            old_end = int(old.get("end_time", 0))
            if old_start > start + 2500:
                continue
            if _norm(old.get("text", "")) == _norm(text) and start <= old_end + 1500:
                duplicate = True
                break
        if not duplicate:
            item["start_time"], item["end_time"] = max(0, start), max(start, end)
            merged.append(item)
    return merged


def transcribe_audio_capcut(
    audio_path: str,
    *,
    language: str = "auto",
    chunk_seconds: float | None = None,
    overlap_seconds: float | None = None,
    max_workers: int | None = None,
    on_progress: callable = None,
) -> list[dict]:
    """Transcribe audio file using CapCut Online STT API and return standard segments."""
    if not audio_path or not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    if chunk_seconds is None:
        try:
            chunk_seconds = float(os.getenv("CAPCUT_STT_CHUNK_SECONDS", "300.0"))
        except (TypeError, ValueError):
            chunk_seconds = 300.0
    if overlap_seconds is None:
        try:
            overlap_seconds = float(os.getenv("CAPCUT_STT_OVERLAP_SECONDS", "2.0"))
        except (TypeError, ValueError):
            overlap_seconds = 2.0
    if max_workers is None:
        try:
            max_workers = int(os.getenv("CAPCUT_STT_WORKERS", "5"))
        except (TypeError, ValueError):
            max_workers = 5

    total_duration = _probe_duration_seconds(audio_path)
    if total_duration <= 0.0:
        total_duration = 3600.0

    if on_progress:
        on_progress("[CapCut STT] Connecting to CapCut Cloud Speech-to-Text...")

    base_device = _build_stt_device()
    session = requests.Session()
    creds = _direct_upload_credentials(base_device, session)

    temp_dir = tempfile.mkdtemp(prefix="capcut_stt_")
    try:
        # Determine chunk plan
        if total_duration <= chunk_seconds + 5.0:
            # Single chunk
            chunk_file = Path(temp_dir) / "single_chunk.mp3"
            _extract_chunk_mp3(audio_path, str(chunk_file), 0.0, total_duration)
            if on_progress:
                on_progress("[CapCut STT] Uploading audio to cloud...")
            store_uri = _direct_vod_upload(base_device, session, chunk_file, creds)
            if on_progress:
                on_progress("[CapCut STT] Recognizing speech...")
            utterances = _direct_query(base_device, session, store_uri, int(total_duration * 1000))
            raw_items = utterances
        else:
            # Multi-chunk parallel processing
            step = chunk_seconds - overlap_seconds
            starts = []
            cur = 0.0
            while cur < total_duration:
                dur = min(chunk_seconds, total_duration - cur)
                starts.append((cur, dur))
                if cur + dur >= total_duration:
                    break
                cur += step

            if on_progress:
                on_progress(f"[CapCut STT] Processing {len(starts)} chunks ({chunk_seconds:.0f}s each)...")

            def _process_one_chunk(idx: int, s: float, d: float):
                worker_device = _new_worker_identity(base_device)
                w_session = requests.Session()
                chunk_file = Path(temp_dir) / f"chunk_{idx:04d}.mp3"
                _extract_chunk_mp3(audio_path, str(chunk_file), s, d)
                try:
                    w_store_uri = _direct_vod_upload(worker_device, w_session, chunk_file, creds)
                    w_utterances = _direct_query(worker_device, w_session, w_store_uri, int(d * 1000))
                    adjusted = []
                    for item in w_utterances:
                        copy_item = dict(item)
                        copy_item["start_time"] = int(item.get("start_time", 0)) + int(s * 1000)
                        copy_item["end_time"] = int(item.get("end_time", 0)) + int(s * 1000)
                        copy_item["_chunk"] = idx
                        adjusted.append(copy_item)
                    return idx, adjusted
                finally:
                    w_session.close()

            completed_chunks = []
            workers_count = min(max_workers, len(starts))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers_count) as executor:
                futures = {
                    executor.submit(_process_one_chunk, i, s, d): i
                    for i, (s, d) in enumerate(starts)
                }
                for fut in concurrent.futures.as_completed(futures):
                    res = fut.result()
                    completed_chunks.append(res)
                    if on_progress:
                        on_progress(f"[CapCut STT] Finished chunk {res[0] + 1}/{len(starts)}")

            raw_items = _merge_chunks(completed_chunks)

        # Convert to CapCap segment format
        segments = []
        for item in raw_items:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            start_s = round(float(item.get("start_time", 0)) / 1000.0, 3)
            end_s = round(float(item.get("end_time", 0)) / 1000.0, 3)
            if end_s <= start_s:
                end_s = start_s + 0.2
            segments.append({
                "start": start_s,
                "end": end_s,
                "text": text,
            })

        if on_progress:
            on_progress(f"[CapCut STT] Recognition complete: {len(segments)} subtitle lines.")
        return segments

    finally:
        session.close()
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
