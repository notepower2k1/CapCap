"""CapCut Online Text-to-Speech (TTS) Integration for CapCap."""

from __future__ import annotations

import io
import json
import os
import secrets
import shutil
import subprocess
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from .transport import create_capcut_session, get_capcut_backend
from .api import (SUCCEEDED, checked_json, check_task_failure, matching_task, request_headers,
                  request_query, task_payload, task_status)

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
        if not v_type or not d_name or item.get("catalog_kind") == "digital_human_voice":
            continue
        # Exclude Microsoft Neural voices that CapCut API no longer proxies
        if "neural" in str(v_type).lower():
            continue

        from language_support import language_code
        lan = language_code(item.get("lan", "vi"))
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


class CapCutVoiceError(ValueError):
    """A voice cannot be mapped to a catalog entry with usable resource metadata."""


def _resolve_voice(voice_type: str) -> dict:
    voices = list_capcut_voices()
    clean_type = voice_type.replace("capcut:", "").strip().lower()
    # Prefer service IDs, then exact display names / resource IDs. Never guess.
    matches = [v for v in voices if v["provider_voice"].lower() == clean_type]
    if not matches:
        matches = [v for v in voices if clean_type in {str(v.get("resource_id", "")).lower(),
                   v["name"].removesuffix(" (CapCut)").strip().lower()}]
    identities = {(v["provider_voice"], str(v.get("resource_id") or "")) for v in matches}
    if len(identities) != 1:
        raise CapCutVoiceError(f"CapCut voice '{voice_type}' is unknown or ambiguous. Select a voice from the CapCut catalog.")
    entry = matches[0]
    if not str(entry.get("resource_id") or "").strip().isdigit():
        raise CapCutVoiceError(f"CapCut voice '{voice_type}' has a missing or invalid resource_id. Update the voice catalog.")
    return entry


def _resolve_resource_id(voice_type: str) -> str:
    return _resolve_voice(voice_type)["resource_id"]


class CapCutTTSClient:
    def __init__(self, session: requests.Session | None = None):
        device_dict = dict(DEFAULT_DEVICE)
        if get_capcut_backend() == "pc":
            device_dict.update(device_platform="windows", device_type="pc", device_brand="pc",
                               channel="capcutpc_0", os_version="10.0.19045")
        device_dict["device_id"] = str(secrets.randbelow(10**20)).zfill(20)
        device_dict["iid"] = str(secrets.randbelow(10**20)).zfill(20)
        device_dict["tdid"] = str(secrets.randbelow(10**20)).zfill(20)
        self.device = DeviceConfig.from_dict(device_dict).to_dict()
        self.session = session if session is not None else create_capcut_session(self.device)
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def _query(self, include_region=True, babi=None):
        return request_query(self.device, include_region, babi)

    def _headers(self, url: str, body_text: str, appid: bool = True):
        return request_headers(self.device, url, body_text, appid)

    def synthesize(
        self,
        texts: list[str],
        voice: str,
        rate: str = "1.0",
        timeout: float = 60.0,
        poll: float = 1.0,
        is_cancelled: callable = None,
    ) -> list[dict]:
        voice_entry = _resolve_voice(voice)
        clean_voice = voice_entry["provider_voice"]
        resource_id = voice_entry["resource_id"]
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
        created = checked_json(resp, "TTS create")

        tasks = (created.get("data") or {}).get("tasks") or []
        if not isinstance(tasks, list) or not tasks or not isinstance(tasks[0], dict):
            raise RuntimeError("CapCut TTS returned no task")
        task_id, token = tasks[0].get("id"), tasks[0].get("token")
        if not task_id or not token:
            raise RuntimeError("CapCut TTS task missing id or token")

        started = time.monotonic()
        actual_timeout = max(float(timeout), len(texts) * 2.5)
        bind_id = tasks[0].get("bind_id") or (created.get("data") or {}).get("bind_id") or ""
        polls = 0
        print(f"[CapCut TTS] Task started job={task_id} items={len(texts)} voice={clean_voice} timeout={actual_timeout:.0f}s", flush=True)
        while time.monotonic() - started < actual_timeout:
            if is_cancelled and is_cancelled():
                return []
            q_body = {"tasks": [{"bind_id": bind_id, "id": task_id, "req_key": "sami_text_to_speech", "task_version": "v3", "token": token}]}
            q_text = signers.compact_json(q_body)
            q_url = f"{CAPCUT_TTS_BASE_URL}/lv/v1/common_task/query?{urlencode(self._query(False))}"
            remaining = actual_timeout - (time.monotonic() - started)
            if remaining <= 0:
                break
            q_resp = self.session.post(q_url, headers=self._headers(q_url, q_text, True), data=q_text.encode("utf-8"), timeout=min(60.0, remaining))
            q_data = checked_json(q_resp, "TTS query")
            polls += 1
            cur = matching_task(q_data, task_id)
            check_task_failure(cur, "TTS", task_id)
            status = task_status(cur)
            if status in SUCCEEDED:
                results = task_payload(cur, "TTS").get("audio_subtitles")
                if not isinstance(results, list) or len(results) != len(texts):
                    raise RuntimeError(f"CapCut TTS incomplete result: job={task_id}, expected={len(texts)}, received={len(results) if isinstance(results, list) else 0}")
                for index, item in enumerate(results):
                    if not isinstance(item, dict) or not isinstance(item.get("speech_url"), str) or not item["speech_url"].strip():
                        raise RuntimeError(f"CapCut TTS missing audio URL: job={task_id}, item={index + 1}")
                    if item.get("code") not in (None, 0, "0", "") or item.get("invalid_input") is True:
                        raise RuntimeError(f"CapCut TTS invalid audio result: job={task_id}, item={index + 1}, code={item.get('code')}")
                print(f"[CapCut TTS] Task completed job={task_id} elapsed={time.monotonic() - started:.2f}s polls={polls} items={len(results)}", flush=True)
                return results
            if polls == 1 or polls % 15 == 0:
                print(f"[CapCut TTS] Task pending job={task_id} elapsed={time.monotonic() - started:.2f}s polls={polls} status={status or 'missing'}", flush=True)
            time.sleep(min(max(0.05, poll), max(0.0, actual_timeout - (time.monotonic() - started))))

        raise TimeoutError(f"CapCut TTS task timed out after {actual_timeout:.0f} seconds (job={task_id}, polls={polls}, items={len(texts)})")


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

    clean_voice = voice_id.replace("capcut:", "").strip()

    # Route Microsoft Edge Neural voices to Edge TTS directly
    if "neural" in clean_voice.lower():
        try:
            from app.tts_processor import edge_tts_to_wav_16k_mono
        except ImportError:
            from tts_processor import edge_tts_to_wav_16k_mono
        rate_pct = f"{int(round((speed - 1.0) * 100)):+d}%"
        return edge_tts_to_wav_16k_mono(
            text=clean_text,
            wav_path=wav_path,
            voice=clean_voice,
            rate=rate_pct,
            tmp_dir=tmp_dir,
        )

    rate_str = f"{speed:.2f}"
    if is_cancelled and is_cancelled():
        return ""
    if on_progress:
        on_progress(f"[CapCut TTS] Synthesizing '{clean_text[:40]}...'")

    client = CapCutTTSClient()
    session = client.session
    try:
        try:
            results = client.synthesize([clean_text], voice=voice_id, rate=rate_str, is_cancelled=is_cancelled)
        except RuntimeError as exc:
            if "TTSInvalidSpeaker" in str(exc) or "40402004" in str(exc):
                try:
                    from app.tts_processor import edge_tts_to_wav_16k_mono
                    rate_pct = f"{int(round((speed - 1.0) * 100)):+d}%"
                    return edge_tts_to_wav_16k_mono(
                        text=clean_text,
                        wav_path=wav_path,
                        voice=clean_voice,
                        rate=rate_pct,
                        tmp_dir=tmp_dir,
                    )
                except Exception:
                    pass
            raise
        if is_cancelled and is_cancelled():
            return ""
        if not results or not results[0].get("speech_url"):
            raise RuntimeError("CapCut TTS returned no audio url")
        audio_url = results[0]["speech_url"]
        # Download MP3 directly in memory
        resp = session.get(audio_url, timeout=60)
        resp.raise_for_status()

        if is_cancelled and is_cancelled():
            return ""
        # Convert MP3 to 16kHz mono WAV in-process
        _convert_mp3_to_wav_16k_mono(resp.content, wav_path)
        return wav_path

    finally:
        session.close()


def _convert_mp3_to_wav_16k_mono(source: Any, wav_path: str) -> str:
    """Convert MP3 bytes or file to 16kHz mono 16-bit PCM WAV."""
    os.makedirs(os.path.dirname(os.path.abspath(wav_path)), exist_ok=True)
    try:
        import av
        import soundfile as sf
        import numpy as np

        bio = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
        container = av.open(bio)
        if container.streams.audio:
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
            parts = []
            for frame in container.decode(stream):
                for rf in resampler.resample(frame):
                    parts.append(rf.to_ndarray().flatten())
            for rf in resampler.resample(None):
                parts.append(rf.to_ndarray().flatten())
            container.close()
            if parts:
                out_pcm = np.concatenate(parts)
                part_path = f"{wav_path}.{uuid.uuid4().hex[:8]}.part.wav"
                try:
                    sf.write(part_path, out_pcm, 16000, format="WAV", subtype="PCM_16")
                    info = sf.info(part_path)
                    if info.frames <= 0:
                        raise RuntimeError(f"Generated WAV file is invalid: {part_path}")
                    os.replace(part_path, wav_path)
                    return wav_path
                finally:
                    if os.path.exists(part_path):
                        try:
                            os.remove(part_path)
                        except OSError:
                            pass
    except (ImportError, ModuleNotFoundError):
        pass
    except (av.FFmpegError, av.error.InvalidDataError):
        pass

    # Fallback to FFmpeg CLI only if PyAV is missing or failed (streams via stdin pipe:0)
    ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
    if not ffmpeg or not os.path.exists(ffmpeg):
        import shutil
        ffmpeg = shutil.which("ffmpeg") or "ffmpeg"

    is_bytes = isinstance(source, (bytes, bytearray, io.BytesIO))
    raw_input_bytes = (
        source.getvalue() if isinstance(source, io.BytesIO) else bytes(source)
    ) if is_bytes else None
    input_arg = "pipe:0" if is_bytes else str(source)

    part_path = f"{wav_path}.{uuid.uuid4().hex[:8]}.part.wav"
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel", "error",
        "-i", input_arg,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "pcm_s16le",
        part_path,
    ]
    try:
        proc = subprocess.run(
            cmd,
            input=raw_input_bytes,
            capture_output=True,
            **subprocess_hidden_kwargs(),
        )
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(f"FFmpeg conversion failed:\n{err}")
        os.replace(part_path, wav_path)
        return wav_path
    finally:
        if os.path.exists(part_path):
            try:
                os.remove(part_path)
            except OSError:
                pass


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
        client = CapCutTTSClient()
        session = client.session
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

                try:
                    resp = session.get(audio_url, timeout=60)
                    resp.raise_for_status()
                    if not (is_cancelled and is_cancelled()):
                        _convert_mp3_to_wav_16k_mono(resp.content, job["wav_path"])
                        return True
                    return False
                except Exception as e:
                    if is_cancelled and is_cancelled():
                        return False
                    raise e

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

    except CapCutVoiceError:
        raise
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
                raise

        single_workers = max(1, min(4, len(valid_jobs)))
        with ThreadPoolExecutor(max_workers=single_workers) as single_executor:
            futures = [single_executor.submit(_synth_single, j) for j in valid_jobs]
            errors = []
            for fut in futures:
                if is_cancelled and is_cancelled():
                    try:
                        single_executor.shutdown(wait=False, cancel_futures=True)
                    except Exception:
                        pass
                    return []
                try:
                    fut.result()
                except Exception as single_error:
                    errors.append(single_error)
            if errors:
                raise RuntimeError(f"CapCut TTS failed for {len(errors)}/{len(valid_jobs)} segments; completed audio files retained") from errors[0]

        return valid_jobs
