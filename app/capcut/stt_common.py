"""Language-aware STT through CapCut's common-task speech endpoint.

Protocol compared with K07VN/capcut-tts-api; implemented using CapCap's
existing signing, transport and bounded chunk recovery.
"""
import time
from urllib.parse import urlencode
import uuid

from .api import (SUCCEEDED, checked_json, check_task_failure, matching_task,
                  request_headers, request_query, task_payload, task_status)
from .signing import compact_json

BASE_URL = "https://editor-api-sg.capcutapi.com"
LANGUAGES = {"zh": "zh-CN", "en": "en-US", "vi": "vi-VN", "ja": "ja-JP", "ko": "ko-KR",
             "th": "th-TH", "ru": "ru-RU", "fr": "fr-FR", "de": "de-DE"}


def normalize_language(language: str) -> str:
    value = str(language or "auto").strip().lower().replace("_", "-")
    if value in {"", "auto"}:
        return "auto"
    if value in {"zh-tw", "zh-hant", "zh-hk"}:
        return "zh-TW" if value != "zh-hk" else "zh-HK"
    base = value.split("-")[0]
    base = {"jp": "ja", "kr": "ko"}.get(base, base)
    if base not in LANGUAGES:
        raise ValueError(f"Unsupported CapCut STT language: {language}")
    # Preserve a valid explicit locale, e.g. en-GB, rather than replacing it.
    if "-" in value and len(value.split("-")) == 2 and len(value.split("-")[1]) == 2:
        return base + "-" + value.split("-")[1].upper()
    return LANGUAGES[base]


def query_common_stt(device, session, audio: dict, duration_ms: int, language: str, *, timeout=None, poll_interval=1.0):
    device = device.to_dict()
    timeout = float(timeout if timeout is not None else min(600, max(120, duration_ms / 1000 * 1.5)))
    babi = {"feature_entrance": "editor", "feature_entrance_detail": "editor-elements-captions-subtitle_recognition",
            "feature_key": "subtitle_recognition", "scenario": "video_editor"}
    caption = {"adjust_endtime": 200, "audio": audio["vid"], "audio_type": "vid", "caption_type": 0,
               "client_request_id": str(uuid.uuid4()), "duration": duration_ms, "enable_cache": True,
               "enter_from": "asr", "language": normalize_language(language), "max_lines": 1,
               "md5": audio["md5"], "pack_options": {"need_attribute": True}, "words_per_line": 15,
               "songs_info": [{"end_time": max(0, duration_ms - 10), "id": "", "start_time": 0}],
               "use_translation": False}
    body = {"bind_id": str(uuid.uuid4()).upper(), "can_queue": True, "enter_from": "asr", "tasks": [{
            "context": str(uuid.uuid4()), "payload": compact_json({"cap_json": caption}),
            "req_key": "cc_audio_subtitle_asr", "task_version": "v3"}]}

    def post(path, body, *, include_region=True, babi=None, request_timeout=60):
        url = BASE_URL + path + "?" + urlencode(request_query(device, include_region, babi))
        text = compact_json(body)
        return checked_json(session.post(url, headers=request_headers(device, url, text, appid=False),
                                         data=text.encode("utf-8"), timeout=request_timeout), "STT common " + path.rsplit("/", 1)[-1])

    created = post("/lv/v1/common_task/new", body, babi=babi)
    tasks = (created.get("data") or {}).get("tasks") or []
    if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
        raise RuntimeError("CapCut STT common create: no task returned")
    job, token = tasks[0].get("id"), tasks[0].get("token")
    if not job or not token:
        raise RuntimeError("CapCut STT common create: missing id or token")
    bind_id = tasks[0].get("bind_id") or (created.get("data") or {}).get("bind_id") or ""
    started, polls = time.monotonic(), 0
    print(f"[CapCut STT] Common task started job={job} language={caption['language']} audio_seconds={duration_ms / 1000:.2f} timeout={timeout:.0f}s", flush=True)
    while time.monotonic() - started < timeout:
        remaining = timeout - (time.monotonic() - started)
        if remaining <= 0:
            break
        result = post("/lv/v1/common_task/query", {"tasks": [{"bind_id": bind_id, "id": job, "token": token,
                      "req_key": "cc_audio_subtitle_asr", "task_version": "v3"}]}, include_region=False, request_timeout=min(60, remaining))
        polls += 1
        task = matching_task(result, job)
        check_task_failure(task, "STT", job)
        status = task_status(task)
        if status in SUCCEEDED:
            items = task_payload(task, "STT").get("utterances")
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise RuntimeError(f"CapCut STT common query: invalid utterances (job={job})")
            print(f"[CapCut STT] Common task completed job={job} elapsed={time.monotonic() - started:.2f}s polls={polls} utterances={len(items)}", flush=True)
            return items
        if polls == 1 or polls % 15 == 0:
            print(f"[CapCut STT] Common task pending job={job} elapsed={time.monotonic() - started:.2f}s polls={polls} status={status or 'missing'}", flush=True)
        time.sleep(min(max(0.05, poll_interval), max(0, timeout - (time.monotonic() - started))))
    raise TimeoutError(f"CapCut STT common task timed out after {timeout:.0f}s (job={job}, polls={polls}, language={caption['language']})")
