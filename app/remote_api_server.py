from __future__ import annotations

import base64
import errno
import json
import os
import sys
import tempfile
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

os.environ.setdefault("CAPCAP_RUNTIME_PROFILE", "local")


def _configure_worker_text_streams() -> None:
    """Keep Unicode paths in worker logs from aborting a Windows workflow.

    A PyInstaller worker launched from a console can inherit a legacy Windows
    code page such as CP1252.  The workflow logs full FFmpeg commands, which
    include the source path; ``print()`` would then raise ``UnicodeEncodeError``
    for Chinese, Vietnamese, and other non-ANSI path characters.  Configure
    the process streams before importing workflow modules so every existing
    diagnostic print is safe.  If a custom stream cannot be reconfigured, its
    replacement policy still prevents logging from becoming a job failure.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (OSError, ValueError, TypeError):
            try:
                reconfigure(errors="backslashreplace")
            except (OSError, ValueError, TypeError):
                pass


_configure_worker_text_streams()

_QUIET = os.getenv("CAPCAP_QUIET", "").strip().lower() in ("1", "true", "yes")
_GPU_LOCK = threading.Lock()


def _log(msg: str):
    if not _QUIET:
        print(msg)

from remote_api import remote_api_token
from services import WorkflowRuntime
from translation.srt_utils import to_srt
from translator import (
    rewrite_translated_segments,
    translate_segments,
    translate_segments_to_srt,
)
from tts_processor import synthesize_text_to_wav_16k_mono
from whisper_processor import transcribe_audio


WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_STATUS_LOCK = threading.Lock()
_STATUS = {"phase": "idle", "message": "Idle"}


def _set_status(phase: str, message: str = "") -> None:
    with _STATUS_LOCK:
        _STATUS["phase"] = str(phase or "idle")
        _STATUS["message"] = str(message or _STATUS["phase"])


def _get_status() -> dict:
    with _STATUS_LOCK:
        return dict(_STATUS)


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status_code)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError, TimeoutError, OSError) as exc:
        if isinstance(exc, OSError) and exc.errno not in {
            errno.EPIPE,
            errno.ECONNABORTED,
            errno.ECONNRESET,
            10053,
            10054,
        }:
            raise


class CapCapRemoteHandler(BaseHTTPRequestHandler):
    server_version = "CapCapRemote/1.0"

    def do_GET(self):
        try:
            if self.path == "/health":
                _json_response(
                    self,
                    200,
                    {
                        "ok": True,
                        "service": "capcap-remote-api",
                        "profile": os.getenv("CAPCAP_RUNTIME_PROFILE", "local"),
                    },
                )
                return
            if self.path == "/v1/status":
                self._check_auth()
                payload = _get_status()
                payload["ok"] = True
                _json_response(self, 200, payload)
                return
            _json_response(self, 404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        _log(f"[Remote API] POST {self.path} from {self.address_string()}")
        try:
            self._check_auth()
            payload = self._read_json_body()
            _log(f"[Remote API] POST {self.path} payload keys: {list(payload.keys())}")
            if self.path == "/v1/transcribe":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_transcribe(payload))
                return
            if self.path == "/v1/translate-segments":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_translate_segments(payload))
                return
            if self.path == "/v1/translate-srt":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_translate_srt(payload))
                return
            if self.path == "/v1/rewrite-segments":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_rewrite_segments(payload))
                return
            if self.path == "/v1/rewrite-srt":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_rewrite_srt(payload))
                return
            if self.path == "/v1/tts/synthesize":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_tts_synthesize(payload))
                return
            if self.path == "/v1/prepare":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_prepare(payload))
                return
            if self.path == "/v1/voice":
                with _GPU_LOCK:
                    _json_response(self, 200, self._handle_voice(payload))
                return
            if self.path == "/v1/export":
                _json_response(self, 200, self._handle_export(payload))
                return
            _json_response(self, 404, {"ok": False, "error": "Not found"})
        except PermissionError as exc:
            _json_response(self, 401, {"ok": False, "error": str(exc)})
        except Exception as exc:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            _log("[Remote API] Request failed:")
            _log(tb)
            log_paths = [os.path.join(tempfile.gettempdir(), "capcap_remote_api_errors.log")]
            try:
                from runtime_paths import temp_path
                log_paths.insert(0, temp_path("capcap_remote_api_errors.log"))
            except Exception:
                pass
            for log_path in dict.fromkeys(log_paths):
                try:
                    os.makedirs(os.path.dirname(log_path), exist_ok=True)
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"[{datetime.now().isoformat()}] {self.path}\n{tb}\n\n")
                except Exception:
                    pass
            error_message = str(exc)
            if isinstance(exc, UnicodeError) or "charmap codec" in error_message.lower():
                error_message = (
                    "Windows text encoding failed while reading an external tool response. "
                    "Please update CapCap; technical details were saved to capcap_remote_api_errors.log."
                )
            _json_response(self, 500, {"ok": False, "error": error_message})

    def log_message(self, format, *args):
        _log(f"[Remote API] {self.address_string()} - {format % args}")

    def _check_auth(self) -> None:
        expected = remote_api_token()
        if not expected:
            return
        supplied = str(self.headers.get("X-CapCap-Token", "") or "").strip()
        if supplied != expected:
            raise PermissionError("Invalid remote API token.")

    def _read_json_body(self) -> dict:
        raw_length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(raw_length) if raw_length > 0 else b"{}"
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _handle_transcribe(self, payload: dict) -> dict:
        audio_b64 = str(payload.get("audio_b64", "") or "").strip()
        if not audio_b64:
            raise ValueError("audio_b64 is required.")
        audio_bytes = base64.b64decode(audio_b64.encode("ascii"))
        audio_filename = str(payload.get("audio_filename", "remote_input.wav") or "remote_input.wav")
        suffix = os.path.splitext(audio_filename)[1] or ".wav"
        tmp_dir = os.path.join(tempfile.gettempdir(), "capcap_remote_api")
        os.makedirs(tmp_dir, exist_ok=True)
        fd, temp_audio_path = tempfile.mkstemp(prefix="asr_", suffix=suffix, dir=tmp_dir)
        os.close(fd)
        try:
            with open(temp_audio_path, "wb") as handle:
                handle.write(audio_bytes)
            model_name = str(payload.get("model_name", "base") or "base").strip().lower()
            language = str(payload.get("language", "auto") or "auto").strip()
            task = str(payload.get("task", "transcribe") or "transcribe").strip()
            segments = transcribe_audio(temp_audio_path, model_name, language=language, task=task)
            return {"ok": True, "segments": list(segments or [])}
        finally:
            try:
                os.remove(temp_audio_path)
            except Exception:
                pass

    def _handle_translate_segments(self, payload: dict) -> dict:
        segments = list(payload.get("segments") or [])
        result = translate_segments(
            segments,
            src_lang=str(payload.get("src_lang", "auto") or "auto"),
            target_lang=str(payload.get("target_lang", "vi") or "vi"),
            enable_polish=bool(payload.get("enable_polish", True)),
            optimize_subtitles=False,
            style_instruction=str(payload.get("style_instruction", "") or ""),
        )
        return {"ok": True, "segments": list(result or [])}

    def _handle_translate_srt(self, payload: dict) -> dict:
        srt_text = str(payload.get("srt_text", "") or "")
        result = translate_segments_to_srt(
            srt_text,
            src_lang=str(payload.get("src_lang", "auto") or "auto"),
            target_lang=str(payload.get("target_lang", "vi") or "vi"),
            enable_polish=bool(payload.get("enable_polish", True)),
            optimize_subtitles=False,
            style_instruction=str(payload.get("style_instruction", "") or ""),
        )
        return {"ok": True, "srt_text": result}

    def _handle_rewrite_segments(self, payload: dict) -> dict:
        result = rewrite_translated_segments(
            list(payload.get("source_segments") or []),
            list(payload.get("translated_segments") or []),
            src_lang=str(payload.get("src_lang", "auto") or "auto"),
            style_instruction=str(payload.get("style_instruction", "") or ""),
        )
        return {"ok": True, "segments": list(result or [])}

    def _handle_rewrite_srt(self, payload: dict) -> dict:
        segments = rewrite_translated_segments(
            list(payload.get("source_segments") or []),
            list(payload.get("translated_segments") or []),
            src_lang=str(payload.get("src_lang", "auto") or "auto"),
            style_instruction=str(payload.get("style_instruction", "") or ""),
        )
        return {"ok": True, "srt_text": to_srt(list(segments or []))}

    def _handle_tts_synthesize(self, payload: dict) -> dict:
        text = str(payload.get("text", "") or "").strip()
        if not text:
            raise ValueError("text is required.")
        voice = str(payload.get("voice", "ngochuyen") or "ngochuyen").strip()
        try:
            speed = float(payload.get("speed", 1.0) or 1.0)
        except Exception:
            speed = 1.0

        tmp_dir = os.path.join(tempfile.gettempdir(), "capcap_remote_tts")
        os.makedirs(tmp_dir, exist_ok=True)
        fd, temp_wav_path = tempfile.mkstemp(prefix="tts_", suffix=".wav", dir=tmp_dir)
        os.close(fd)
        try:
            synthesize_text_to_wav_16k_mono(
                text=text,
                wav_path=temp_wav_path,
                voice=voice,
                speed=speed,
                tmp_dir=tmp_dir,
            )
            with open(temp_wav_path, "rb") as handle:
                audio_b64 = base64.b64encode(handle.read()).decode("ascii")
            return {"ok": True, "audio_b64": audio_b64}
        finally:
            try:
                os.remove(temp_wav_path)
            except Exception:
                pass

    def _handle_prepare(self, payload: dict) -> dict:
        video_path = str(payload.get("video_path", "") or "").strip()
        if not video_path or not os.path.exists(video_path):
            raise ValueError("video_path required and must exist.")
        workspace_root = str(payload.get("workspace_root", "") or "").strip() or WORKSPACE_ROOT
        runtime = WorkflowRuntime(workspace_root)
        _set_status("prepare", "Preparing project")

        def step_callback(step_id):
            labels = {
                "prepare": "Preparing project",
                "extract_audio": "Extracting audio",
                "extraction": "Extracting audio",
                "separation": "Separating vocals",
                "diarization": "Detecting speakers",
                "transcription": "Transcribing audio",
                "translation": "Translating subtitles",
            }
            _set_status(step_id, labels.get(str(step_id or ""), str(step_id or "Processing")))

        try:
            state = runtime.run_prepare(
                video_path,
                source_language=str(payload.get("source_language", "auto") or "auto"),
                target_language=str(payload.get("target_language", "vi") or "vi"),
                mode=str(payload.get("mode", "subtitle") or "subtitle"),
                audio_handling_mode=str(payload.get("audio_handling_mode", "fast") or "fast"),
                translator_ai=bool(payload.get("translator_ai", True)),
                optimize_subtitles=False,
                translator_style=str(payload.get("translator_style", "") or ""),
                whisper_model_name=str(payload.get("whisper_model_name", "base") or "base"),
                transcription_engine=str(payload.get("transcription_engine", "whisper") or "whisper"),
                speaker_diarization=bool(payload.get("speaker_diarization", False)),
                speaker_diarization_num_speakers=int(payload.get("speaker_diarization_num_speakers", -1) or -1),
                skip_translation=bool(payload.get("skip_translation", False)),
                prefetch_voice_name=str(payload.get("prefetch_voice_name", "") or ""),
                prefetch_voice_speed=float(payload.get("prefetch_voice_speed", 1.0) or 1.0),
                step_callback=step_callback,
            )
            _set_status("done", "Prepare complete")
            return {"ok": True, "project_state_path": runtime.project_state_path(state)}
        except Exception:
            _set_status("error", "Prepare failed")
            raise
        finally:
            _unload_models()

    def _handle_voice(self, payload: dict) -> dict:
        _log(f"[Remote API] _handle_voice called. voice_name={payload.get('voice_name')}, segments_count={len(payload.get('segments', []))}")
        workspace_root = str(payload.get("workspace_root", "") or "").strip() or WORKSPACE_ROOT
        runtime = WorkflowRuntime(workspace_root)
        result = runtime.run_voice(
            segments=list(payload.get("segments") or []),
            output_dir=str(payload.get("output_dir", "") or ""),
            background_path=str(payload.get("background_path", "") or ""),
            audio_handling_mode=str(payload.get("audio_handling_mode", "fast") or "fast"),
            voice_name=str(payload.get("voice_name", "ngochuyen") or "ngochuyen"),
            voice_speed=float(payload.get("voice_speed", 1.0) or 1.0),
            timing_sync_mode=str(payload.get("timing_sync_mode", "off") or "off"),
            original_volume=int(payload.get("original_volume", 50) or 50),
            dub_volume=int(payload.get("dub_volume", 100) or 100),
            project_state_path=str(payload.get("project_state_path", "") or ""),
            project_temp_dir=str(payload.get("project_temp_dir", "") or ""),
            ai_rewrite_dubbing=bool(payload.get("ai_rewrite_dubbing", False)),
            dubbing_style_instruction=str(payload.get("dubbing_style_instruction", "") or ""),
            source_language=str(payload.get("source_language", "auto") or "auto"),
        )
        _unload_whisper()
        return {"ok": True, "result": result}

    def _handle_export(self, payload: dict) -> dict:
        workspace_root = str(payload.get("workspace_root", "") or "").strip() or WORKSPACE_ROOT
        runtime = WorkflowRuntime(workspace_root)
        output = runtime.run_export(
            video_path=str(payload.get("video_path", "") or ""),
            output_path=str(payload.get("output_path", "") or ""),
            mode=str(payload.get("mode", "subtitle") or "subtitle"),
            srt_path=str(payload.get("srt_path", "") or ""),
            ass_path=str(payload.get("ass_path", "") or ""),
            audio_path=str(payload.get("audio_path", "") or ""),
            subtitle_style=dict(payload.get("subtitle_style") or {}),
            output_quality=str(payload.get("output_quality", "source") or "source"),
            output_fps=str(payload.get("output_fps", "source") or "source"),
            output_ratio=str(payload.get("output_ratio", "source") or "source"),
            output_scale_mode=str(payload.get("output_scale_mode", "fit") or "fit"),
            output_fill_focus_x=float(payload.get("output_fill_focus_x", 0.5) or 0.5),
            output_fill_focus_y=float(payload.get("output_fill_focus_y", 0.5) or 0.5),
            video_filter_state=dict(payload.get("video_filter_state") or {}),
            original_audio_gain_db=float(payload.get("original_audio_gain_db", 0.0) or 0.0),
            project_state_path=str(payload.get("project_state_path", "") or ""),
            project_temp_dir=str(payload.get("project_temp_dir", "") or ""),
        )
        return {"ok": True, "output_path": output}


def _unload_whisper():
    try:
        from whisper_processor import unload_whisper_models
        unload_whisper_models()
    except Exception:
        pass


def _unload_models():
    _unload_whisper()


def main() -> None:
    host = str(os.getenv("CAPCAP_REMOTE_API_HOST", "0.0.0.0") or "0.0.0.0").strip()
    port_raw = str(os.getenv("CAPCAP_REMOTE_API_PORT", "8765") or "8765").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 8765

    preload_models = str(os.getenv("CAPCAP_REMOTE_PRELOAD_MODELS", "1") or "1").strip().lower()
    if preload_models not in {"0", "false", "no", "off"}:
        _log("[Remote API] Pre-loading Whisper model on main thread...")
        model_name = os.getenv("CAPCAP_WHISPER_MODEL_NAME", "base")
        try:
            from whisper_processor import _load_whisper_model
            _load_whisper_model(model_name)
            _log(f"[Remote API] Whisper '{model_name}' loaded on main thread.")
        except Exception as exc:
            _log(f"[Remote API] Whisper pre-load failed (will load on demand): {exc}")
            _log("[Remote API] Tip: set CAPCAP_WHISPER_DEVICE=cpu in .env if CUDA hangs in thread.")

    server = ThreadingHTTPServer((host, port), CapCapRemoteHandler)
    _log(f"[Remote API] Listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
