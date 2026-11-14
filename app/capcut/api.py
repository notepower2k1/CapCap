"""Response validation shared by CapCut speech clients, without logging payloads."""
from __future__ import annotations

import json
import re
import time
from . import signing


def request_query(device: dict, include_region=True, babi=None) -> dict:
    query = {key: device[key] for key in ("app_name", "device_type", "os_version", "channel", "version_name",
             "device_brand", "device_id", "iid", "version_code", "device_platform", "aid")}
    if include_region:
        query["region"] = device["region"]
    if babi is not None:
        query["babi_param"] = signing.compact_json(babi)
    return query


def request_headers(device: dict, url: str, body: str, appid=True) -> dict:
    now = str(int(time.time()))
    headers = {"content-type": "application/json", "appvr": device["appvr"], "ch": device["channel"],
               "device-time": now, "lan": device["lan"], "loc": device["loc"], "pf": device["pf"],
               "sign-ver": "1", "tdid": device["tdid"], "x-ss-stub": signing.make_x_ss_stub(body),
               "x-ss-dp": device["aid"], "x-khronos": now, "x-tt-trace-id": signing.make_trace_id(),
               "user-agent": "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03",
               "accept-encoding": "gzip, deflate"}
    if appid:
        headers.update({"app-sdk-version": device["appvr"], "appid": device["aid"]})
    headers["sign"] = signing.make_sign_header(url, device["appvr"], now, device["tdid"], device["pf"])
    return headers

SUCCEEDED = frozenset({"success", "succeed", "succeeded", "completed", "done"})
FAILED = frozenset({"fail", "failed", "failure", "error", "cancelled", "canceled", "rejected", "expired"})
PENDING = frozenset({"processing", "pending", "queued", "running", "waiting", "in_progress", "created"})


def safe_detail(value) -> str:
    if value is not None and not isinstance(value, (str, int, float)):
        return "[structured detail omitted]"
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = re.sub(r"https?://\S+", "[URL]", text)
    text = re.sub(r"(?i)(bearer\s+|(?:token|cookie|authorization|secret(?:_access_key)?)[\"']?\s*[=:]\s*[\"']?)[^\s,;\"'}]+", r"\1[redacted]", text)
    return text[:240]


def checked_json(response, stage: str) -> dict:
    # Preserve requests.HTTPError for the existing bounded retry policy.
    response.raise_for_status()
    try:
        result = response.json()
    except (ValueError, TypeError) as exc:
        raise RuntimeError(f"CapCut {stage}: invalid JSON (HTTP {response.status_code})") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"CapCut {stage}: expected a JSON object (HTTP {response.status_code})")
    ret = result.get("ret")
    if ret not in (None, 0, "0", ""):
        message = safe_detail(result.get("errmsg") or result.get("message"))
        request_id = safe_detail(result.get("log_id") or result.get("request_id"))
        raise RuntimeError(f"CapCut {stage}: ret={safe_detail(ret)} msg={message} request_id={request_id}")
    if result.get("data") is not None and not isinstance(result["data"], dict):
        raise RuntimeError(f"CapCut {stage}: malformed data object (HTTP {response.status_code})")
    return result


def task_status(task: dict) -> str:
    return str(task.get("status") or task.get("task_status") or "").strip().lower()


def check_task_failure(task: dict, stage: str, task_id: str):
    status = task_status(task)
    if status in FAILED:
        code = task.get("err_code", task.get("error_code"))
        message = safe_detail(task.get("err_msg") or task.get("errmsg") or task.get("detail_info") or status)
        raise RuntimeError(f"CapCut {stage} task failed: {message} (code {safe_detail(code)}, job={task_id}, status={status})")


def task_payload(task: dict, stage: str) -> dict:
    payload = task.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError as exc:
            raise RuntimeError(f"CapCut {stage}: invalid task payload JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"CapCut {stage}: task payload must be an object")
    return payload


def matching_task(data: dict, task_id: str) -> dict:
    tasks = (data.get("data") or {}).get("tasks") or []
    if not isinstance(tasks, list) or any(not isinstance(task, dict) for task in tasks):
        raise RuntimeError("CapCut query: malformed tasks list")
    for task in tasks:
        if str(task.get("id", "")) == str(task_id):
            return task
    # Older responses can omit id when exactly one task was queried.
    if len(tasks) == 1 and not tasks[0].get("id"):
        return tasks[0]
    return {}
