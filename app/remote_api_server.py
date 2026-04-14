from __future__ import annotations

import base64
import json
import os
import tempfile
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault("CAPCAP_RUNTIME_PROFILE", "local")

from remote_api import remote_api_token
from translation.srt_utils import to_srt
from translator import (
    rewrite_translated_segments,
    translate_segments,
    translate_segments_to_srt,
)
from tts_processor import synthesize_text_to_wav_16k_mono
from whisper_processor import transcribe_audio


def _json_response(handler: BaseHTTPRequestHandler, status_code: int, payload: dict) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status_code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


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
            _json_response(self, 404, {"ok": False, "error": "Not found"})
        except Exception as exc:
            _json_response(self, 500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        try:
            self._check_auth()
            payload = self._read_json_body()
            if self.path == "/v1/transcribe":
                _json_response(self, 200, self._handle_transcribe(payload))
                return
            if self.path == "/v1/translate-segments":
                _json_response(self, 200, self._handle_translate_segments(payload))
                return
            if self.path == "/v1/translate-srt":
                _json_response(self, 200, self._handle_translate_srt(payload))
                return
            if self.path == "/v1/rewrite-segments":
                _json_response(self, 200, self._handle_rewrite_segments(payload))
                return
            if self.path == "/v1/rewrite-srt":
                _json_response(self, 200, self._handle_rewrite_srt(payload))
                return
            if self.path == "/v1/tts/synthesize":
                _json_response(self, 200, self._handle_tts_synthesize(payload))
                return
            _json_response(self, 404, {"ok": False, "error": "Not found"})
        except PermissionError as exc:
            _json_response(self, 401, {"ok": False, "error": str(exc)})
        except Exception as exc:
            print("[Remote API] Request failed:")
            print("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip())
            _json_response(self, 500, {"ok": False, "error": str(exc)})

    def log_message(self, format, *args):
        print(f"[Remote API] {self.address_string()} - {format % args}")

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
            enable_polish=bool(payload.get("enable_polish", True)),
            optimize_subtitles=bool(payload.get("optimize_subtitles", True)),
            style_instruction=str(payload.get("style_instruction", "") or ""),
        )
        return {"ok": True, "segments": list(result or [])}

    def _handle_translate_srt(self, payload: dict) -> dict:
        srt_text = str(payload.get("srt_text", "") or "")
        result = translate_segments_to_srt(
            srt_text,
            src_lang=str(payload.get("src_lang", "auto") or "auto"),
            enable_polish=bool(payload.get("enable_polish", True)),
            optimize_subtitles=bool(payload.get("optimize_subtitles", True)),
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
        voice = str(payload.get("voice", "vi_VN-vais1000-medium") or "vi_VN-vais1000-medium").strip()
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


def main() -> None:
    host = str(os.getenv("CAPCAP_REMOTE_API_HOST", "0.0.0.0") or "0.0.0.0").strip()
    port_raw = str(os.getenv("CAPCAP_REMOTE_API_PORT", "8765") or "8765").strip()
    try:
        port = int(port_raw)
    except Exception:
        port = 8765
    server = ThreadingHTTPServer((host, port), CapCapRemoteHandler)
    print(f"[Remote API] Listening on http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
