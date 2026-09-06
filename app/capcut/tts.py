"""CapCut Online Text-to-Speech (TTS) Integration for CapCap."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter

from runtime_paths import app_path, bin_path, bundle_root, subprocess_hidden_kwargs
try:
    from app.capcut.device import DeviceConfig, DEFAULT_DEVICE
    import app.capcut.signing as signers
except ImportError:
    from .device import DeviceConfig, DEFAULT_DEVICE
    from . import signing as signers

CAPCUT_TTS_BASE_URL = "https://editor-api-sg.capcutapi.com"
_CACHED_CAPCUT_CATALOG = None


def _get_voice_catalog_file() -> str:
    candidates = [
        app_path("assets", "capcut", "Voice.json"),
        os.path.join(bundle_root(), "assets", "capcut", "Voice.json"),
        os.path.join(os.getcwd(), "assets", "capcut", "Voice.json"),
        r"D:\CodingTime\CapCutOnlineApi\Voice.json",
    ]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return candidates[0]


def list_capcut_voices() -> list[dict]:
    """Return all CapCut voices formatted for CapCap voice preview catalog."""
    global _CACHED_CAPCUT_CATALOG
    if _CACHED_CAPCUT_CATALOG is not None:
        return _CACHED_CAPCUT_CATALOG

    catalog_file = _get_voice_catalog_file()
    if not os.path.exists(catalog_file):
        return []

    try:
        with open(catalog_file, "r", encoding="utf-8") as f:
            raw_list = json.load(f)
    except Exception as e:
        print(f"[CapCut TTS] Error reading Voice.json: {e}")
        return []

    result = []
    for item in raw_list:
        v_type = item.get("voice_type")
        d_name = item.get("display_name")
        if not v_type or not d_name:
            continue

        lan = str(item.get("lan", "vi")).strip().lower()
        res_id = str(item.get("resource_id", "")).strip()

        # Simple gender heuristic from display name
        lower_name = d_name.lower()
        if any(w in lower_name for w in ("nữ", "cô", "bé gái", "female", "girl")):
            gender = "female"
        elif any(w in lower_name for w in ("nam", "ông", "chú", "anh", "male", "boy")):
            gender = "male"
        else:
            gender = "female" if lan == "vi" and "hoai my" in lower_name else ("male" if "nam minh" in lower_name else "any")

        result.append({
            "id": f"capcut:{v_type}",
            "name": f"{d_name} (CapCut)",
            "provider": "capcut",
            "provider_voice": v_type,
            "resource_id": res_id,
            "language": lan,
            "gender": gender,
            "tier": "free",
            "preview_video_url": "",
            "preview_video_path": "",
            "preview_audio_url": "",
            "preview_audio_path": "",
            "enabled": True,
            "tags": ["capcut", "cloud", lan],
            "description": f"CapCut Cloud Voice: {d_name}",
        })

    _CACHED_CAPCUT_CATALOG = result
    return result


def _resolve_resource_id(voice_type: str) -> str:
    voices = list_capcut_voices()
    clean_type = voice_type.replace("capcut:", "").strip().lower()
    for v in voices:
        if v["provider_voice"].lower() == clean_type or v["id"].lower() == voice_type.lower():
            return v.get("resource_id", "")
    return "7252594014782755330" if "vivn" in clean_type else "7102355709945188865"


class CapCutTTSClient:
    def __init__(self, session: requests.Session | None = None):
        device_dict = dict(DEFAULT_DEVICE)
        device_dict["device_id"] = str(secrets.randbelow(10**20)).zfill(20)
        device_dict["iid"] = str(secrets.randbelow(10**20)).zfill(20)
        device_dict["tdid"] = str(secrets.randbelow(10**20)).zfill(20)
        self.device = DeviceConfig.from_dict(device_dict).to_dict()
        self.session = session or requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _query(self, include_region=True, babi=None):
        d = self.device
        q = {
            k: d[k]
            for k in (
                "app_name", "device_type", "os_version", "channel", "version_name",
                "device_brand", "device_id", "iid", "version_code", "device_platform", "aid",
            )
        }
        if include_region:
            q["region"] = d["region"]
        if babi is not None:
            q["babi_param"] = signers.compact_json(babi)
        return q

    def _headers(self, url: str, body_text: str, appid: bool = True):
        d = self.device
        now = str(int(time.time()))
        h = {
            "content-type": "application/json",
            "appvr": d["appvr"],
            "ch": d["channel"],
            "device-time": now,
            "lan": d["lan"],
            "loc": d["loc"],
            "pf": d["pf"],
            "sign-ver": "1",
            "tdid": d["tdid"],
            "x-ss-stub": signers.make_x_ss_stub(body_text),
            "x-ss-dp": d["aid"],
            "x-khronos": now,
            "x-tt-trace-id": signers.make_trace_id(),
            "user-agent": "Cronet/TTNetVersion:1d7cc3b1 2025-07-16 QuicVersion:52c2b40d 2025-04-03",
            "accept-encoding": "gzip, deflate",
        }
        if appid:
            h.update({"app-sdk-version": d["appvr"], "appid": d["aid"]})
        h["sign"] = signers.make_sign_header(url, d["appvr"], now, d["tdid"], d["pf"])
        return h

    def synthesize(
        self,
        texts: list[str],
        voice: str,
        rate: str = "1.0",
        timeout: float = 60.0,
        poll: float = 1.0,
        is_cancelled: callable = None,
    ) -> list[dict]:
        clean_voice = voice.replace("capcut:", "").strip()
        resource_id = _resolve_resource_id(clean_voice)
        d = self.device

        babi = {
            "feature_entrance": "editor",
            "feature_entrance_detail": "editor-feature-text_to_speech",
            "feature_key": "text_to_speech",
            "scenario": "video_editor",
        }
        blocks = []
        for t in texts:
            blocks.append(
                f'<voice name="{signers.escape_xml(clean_voice)}" mock_tone_info="" platform="sami" '
                f'resource_id="{resource_id}" emotion="" emotion_scale="0" style="" role="" '
                f'moyin_emotion="" is_clone_tone="false" need_subtitle_timestamp="false">\n'
                f'<prosody rate="{signers.escape_xml(rate)}">{signers.escape_xml(t)}</prosody>\n</voice>'
            )
        ssml = '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">\n' + "\n".join(blocks) + "\n</speak>"
        extra_info = signers.compact_json({"benefit_info": {}})
        payload = {
            "audio_format": "mp3",
            "babi_param": signers.compact_json(babi),
            "credit_disable": False,
            "extra_info": extra_info,
            "need_merge_voice": False,
            "need_subtitle_timestamp": False,
            "scene": "text_to_speech",
            "ssml": ssml,
        }
        payload["sign"] = signers.make_tts_payload_sign(ssml, extra_info, d["device_id"], d["aid"])
        body = {
            "bind_id": str(uuid.uuid4()),
            "can_queue": True,
            "enter_from": "text_to_speech",
            "tasks": [{
                "context": str(uuid.uuid4()),
                "payload": signers.compact_json(payload),
                "req_key": "sami_text_to_speech",
                "task_version": "v3",
            }],
        }
        body_text = signers.compact_json(body)
        url = f"{CAPCUT_TTS_BASE_URL}/lv/v1/common_task/new?{urlencode(self._query(True, babi))}"
        if is_cancelled and is_cancelled():
            return []
        resp = self.session.post(url, headers=self._headers(url, body_text, True), data=body_text.encode("utf-8"), timeout=60)
        resp.raise_for_status()
        created = resp.json()
        if str(created.get("ret")) not in {"0", "None"}:
            raise RuntimeError(f"CapCut TTS create failed: ret={created.get('ret')} msg={created.get('errmsg')}")

        tasks = (created.get("data") or {}).get("tasks") or []
        if not tasks:
            raise RuntimeError("CapCut TTS returned no task")
        task_id, token = tasks[0].get("id"), tasks[0].get("token")
        if not task_id or not token:
            raise RuntimeError("CapCut TTS task missing id or token")

        started = time.monotonic()
        actual_timeout = max(float(timeout), len(texts) * 2.5)
        while time.monotonic() - started < actual_timeout:
            if is_cancelled and is_cancelled():
                return []
            q_body = {"tasks": [{"bind_id": "", "id": task_id, "req_key": "sami_text_to_speech", "task_version": "v3", "token": token}]}
            q_text = signers.compact_json(q_body)
            q_url = f"{CAPCUT_TTS_BASE_URL}/lv/v1/common_task/query?{urlencode(self._query(False))}"
            q_resp = self.session.post(q_url, headers=self._headers(q_url, q_text, True), data=q_text.encode("utf-8"), timeout=60)
            q_resp.raise_for_status()
            q_data = q_resp.json()
            q_tasks = (q_data.get("data") or {}).get("tasks") or []
            cur = q_tasks[0] if q_tasks else {}
            status = str(cur.get("status", "")).lower()
            if status in {"succeed", "success"}:
                payload_str = cur.get("payload") or "{}"
                if isinstance(payload_str, str):
                    payload_dict = json.loads(payload_str)
                else:
                    payload_dict = payload_str
                results = payload_dict.get("audio_subtitles") or []
                if not isinstance(results, list):
                    raise RuntimeError("CapCut TTS response missing audio_subtitles")
                return results
            if status in {"failed", "fail", "error"}:
                raise RuntimeError(f"CapCut TTS task failed: {cur.get('status')}")
            time.sleep(poll)

        raise TimeoutError(f"CapCut TTS task timed out after {actual_timeout:.0f} seconds")


def synthesize_capcut_tts_wav_16k_mono(
    text: str,
    wav_path: str,
    voice_id: str = "capcut:BV421_vivn_streaming",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
    is_cancelled: callable = None,
) -> str:
    """Synthesize text via CapCut Cloud TTS API and save to 16kHz mono WAV."""
    if is_cancelled and is_cancelled():
        return ""
    clean_text = str(text or "").strip()
    if not clean_text:
        raise ValueError("Text to synthesize is empty.")

    rate_str = f"{speed:.2f}"
    if is_cancelled and is_cancelled():
        return ""
    if on_progress:
        on_progress(f"[CapCut TTS] Synthesizing '{clean_text[:40]}...'")

    session = requests.Session()
    client = CapCutTTSClient(session=session)
    results = client.synthesize([clean_text], voice=voice_id, rate=rate_str, is_cancelled=is_cancelled)
    if is_cancelled and is_cancelled():
        session.close()
        return ""
    if not results or not results[0].get("speech_url"):
        raise RuntimeError("CapCut TTS returned no audio url")

    audio_url = results[0]["speech_url"]
    temp_dir = tmp_dir or os.path.join(os.path.dirname(wav_path) or ".", "capcut_tmp")
    os.makedirs(temp_dir, exist_ok=True)
    temp_mp3 = os.path.join(temp_dir, f"capcut_{uuid.uuid4().hex[:8]}.mp3")

    try:
        if is_cancelled and is_cancelled():
            return ""
        # Download MP3
        resp = session.get(audio_url, stream=True, timeout=60)
        resp.raise_for_status()
        with open(temp_mp3, "wb") as f:
            for chunk in resp.iter_content(64 * 1024):
                if is_cancelled and is_cancelled():
                    break
                if chunk:
                    f.write(chunk)

        if is_cancelled and is_cancelled():
            return ""
        # Convert MP3 to 16kHz mono WAV
        _convert_mp3_to_wav_16k_mono(temp_mp3, wav_path)
        return wav_path

    finally:
        session.close()
        if os.path.exists(temp_mp3):
            try:
                os.remove(temp_mp3)
            except OSError:
                pass


def _convert_mp3_to_wav_16k_mono(mp3_path: str, wav_path: str) -> str:
    """Convert an audio file (typically MP3) to 16kHz mono 16-bit PCM WAV."""
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
    ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
    if not ffmpeg or not os.path.exists(ffmpeg):
        import shutil
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    cmd = [
        ffmpeg,
        "-y",
        "-loglevel", "error",
        "-i", mp3_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        wav_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, **subprocess_hidden_kwargs())
    return wav_path


def synthesize_capcut_tts_batch(
    batch_jobs: list[dict],
    voice_id: str = "capcut:BV421_vivn_streaming",
    speed: float = 1.0,
    tmp_dir: str | None = None,
    on_progress: callable = None,
    is_cancelled: callable = None,
    _depth: int = 0,
) -> list[dict]:
    """Synthesize a batch of subtitle segment texts via CapCut Cloud TTS API.

    Each item in batch_jobs must have at least 'text' and 'wav_path'.
    Features:
    - Adaptive binary splitting on failure: isolates problematic segments quickly
      while preserving full batch speed for the rest.
    - Concurrent MP3 download and 16kHz mono WAV conversion using a thread pool.
    - Isolated fallback for small batches/single segments with error protection.
    """
    if is_cancelled and is_cancelled():
        return []
    valid_jobs = [job for job in batch_jobs if str(job.get("text") or "").strip()]
    if not valid_jobs:
        return batch_jobs

    rate_str = f"{speed:.2f}"
    texts = [str(j["text"]).strip() for j in valid_jobs]

    try:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        client = CapCutTTSClient(session=session)
        try:
            if on_progress:
                on_progress(f"[CapCut TTS] Synthesizing batch of {len(valid_jobs)} segments...")
            results = client.synthesize(
                texts,
                voice=voice_id,
                rate=rate_str,
                timeout=max(60.0, len(texts) * 2.5),
                is_cancelled=is_cancelled,
            )
            if is_cancelled and is_cancelled():
                return []
            if not isinstance(results, list) or len(results) != len(valid_jobs):
                raise RuntimeError(
                    f"CapCut TTS returned {len(results) if isinstance(results, list) else 0} results for {len(valid_jobs)} items"
                )

            def _download_and_convert(pair):
                if is_cancelled and is_cancelled():
                    return False
                job, res = pair
                audio_url = res.get("speech_url")
                if not audio_url:
                    raise RuntimeError(f"CapCut TTS item missing speech_url for '{job.get('text', '')[:30]}'")

                job_temp_dir = tmp_dir or os.path.join(os.path.dirname(job["wav_path"]) or ".", "capcut_tmp")
                os.makedirs(job_temp_dir, exist_ok=True)
                temp_mp3 = os.path.join(job_temp_dir, f"capcut_{uuid.uuid4().hex[:8]}.mp3")
                try:
                    resp = session.get(audio_url, stream=True, timeout=60)
                    resp.raise_for_status()
                    with open(temp_mp3, "wb") as f:
                        for chunk in resp.iter_content(64 * 1024):
                            if is_cancelled and is_cancelled():
                                return False
                            if chunk:
                                f.write(chunk)
                    if not (is_cancelled and is_cancelled()):
                        _convert_mp3_to_wav_16k_mono(temp_mp3, job["wav_path"])
                        return True
                    return False
                finally:
                    if os.path.exists(temp_mp3):
                        try:
                            os.remove(temp_mp3)
                        except OSError:
                            pass

            pairs = list(zip(valid_jobs, results))
            dl_workers = max(1, min(8, len(pairs)))
            with ThreadPoolExecutor(max_workers=dl_workers) as dl_executor:
                futures = [dl_executor.submit(_download_and_convert, p) for p in pairs]
                for fut in futures:
                    if is_cancelled and is_cancelled():
                        try:
                            dl_executor.shutdown(wait=False, cancel_futures=True)
                        except Exception:
                            pass
                        return []
                    fut.result()

        finally:
            session.close()

        return valid_jobs

    except Exception as exc:
        if is_cancelled and is_cancelled():
            return []

        # If batch is larger than 4, split into two halves (Adaptive Binary Splitting)
        if len(valid_jobs) > 4 and _depth < 5:
            mid = len(valid_jobs) // 2
            left_jobs = valid_jobs[:mid]
            right_jobs = valid_jobs[mid:]
            print(
                f"[CapCut TTS] Batch of {len(valid_jobs)} segments failed ({exc}). "
                f"Binary splitting into sub-batches of {len(left_jobs)} and {len(right_jobs)}..."
            )
            if on_progress:
                on_progress(f"[CapCut TTS] Splitting batch of {len(valid_jobs)} into smaller sub-batches...")

            left_res = synthesize_capcut_tts_batch(
                left_jobs,
                voice_id=voice_id,
                speed=speed,
                tmp_dir=tmp_dir,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                _depth=_depth + 1,
            )
            if is_cancelled and is_cancelled():
                return []

            right_res = synthesize_capcut_tts_batch(
                right_jobs,
                voice_id=voice_id,
                speed=speed,
                tmp_dir=tmp_dir,
                on_progress=on_progress,
                is_cancelled=is_cancelled,
                _depth=_depth + 1,
            )
            return left_res + right_res

        # Small batch fallback (<= 4 segments): synthesize concurrently with per-segment error isolation
        print(f"[CapCut TTS] Small batch fallback: synthesizing {len(valid_jobs)} segments with isolation...")
        def _synth_single(job):
            if is_cancelled and is_cancelled():
                return
            try:
                synthesize_capcut_tts_wav_16k_mono(
                    text=job["text"],
                    wav_path=job["wav_path"],
                    voice_id=voice_id,
                    speed=speed,
                    tmp_dir=tmp_dir,
                    on_progress=on_progress,
                    is_cancelled=is_cancelled,
                )
            except Exception as single_err:
                print(f"[CapCut TTS] Single segment failed ({job.get('text', '')[:30]}): {single_err}")

        single_workers = max(1, min(4, len(valid_jobs)))
        with ThreadPoolExecutor(max_workers=single_workers) as single_executor:
            futures = [single_executor.submit(_synth_single, j) for j in valid_jobs]
            for fut in futures:
                if is_cancelled and is_cancelled():
                    try:
                        single_executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    return []
                try:
                    fut.result()
                except Exception:
                    pass

        return valid_jobs
