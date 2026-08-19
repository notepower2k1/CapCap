import hashlib
import math
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app")
if APP_PATH not in sys.path:
    sys.path.insert(0, APP_PATH)

from runtime_paths import bin_path, subprocess_hidden_kwargs
from services import EngineRuntime, ResourceDownloadService


class VocalSeparationWorker(QThread):
    finished = Signal(str, str, str)

    def __init__(self, audio_path, output_dir):
        super().__init__()
        self.audio_path = audio_path
        self.output_dir = output_dir

    def run(self):
        try:
            engine = EngineRuntime()
            vocal_path, music_path = engine.separate_vocals(self.audio_path, self.output_dir)
            if vocal_path and music_path:
                self.finished.emit(vocal_path, music_path, "")
            else:
                self.finished.emit("", "", "Failed to separate audio stems.")
        except ImportError as exc:
            self.finished.emit("", "", str(exc))
        except Exception as exc:
            self.finished.emit("", "", f"Unexpected error: {str(exc)}")


class ExtractionWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, video_path, audio_output_path):
        super().__init__()
        self.video_path = video_path
        self.audio_output_path = audio_output_path

    def run(self):
        try:
            engine = EngineRuntime()
            success = engine.extract_audio(self.video_path, self.audio_output_path)
            self.finished.emit(success, self.audio_output_path)
        except Exception as exc:
            self.finished.emit(False, str(exc))


class TranscriptionWorker(QThread):
    finished = Signal(list, str)

    def __init__(self, audio_path, model_path, language):
        super().__init__()
        self.audio_path = audio_path
        self.model_path = model_path
        self.language = language

    def run(self):
        try:
            engine = EngineRuntime()
            segments = engine.transcribe_audio(self.audio_path, self.model_path, language=self.language)
            self.finished.emit(segments if segments else [], "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"Transcription Thread Error:\n{details}")
            self.finished.emit([], details or str(exc))


class AlternateRangeTranscriptionWorker(QThread):
    """One-shot alternate-engine transcription for a timeline selection."""
    # Do not shadow QThread.finished.  The native signal is needed to retain
    # and safely dispose of the worker only after run() has actually exited.
    completed = Signal(list, str)

    def __init__(
        self,
        video_path,
        start,
        end,
        engine_name,
        model_path="",
        language="auto",
        *,
        ocr_region="bottom",
        ocr_fps=None,
    ):
        super().__init__()
        self.video_path, self.start_time, self.end_time = video_path, float(start), float(end)
        self.engine_name, self.model_path, self.language = engine_name, model_path, language
        self.ocr_region = str(ocr_region or "bottom")
        self.ocr_fps = float(ocr_fps) if ocr_fps is not None else None

    def run(self):
        temp_audio = ""
        try:
            engine = EngineRuntime()
            if self.engine_name == "ocr":
                segments = engine.transcribe_video_ocr(
                    self.video_path,
                    region=self.ocr_region,
                    fps=self.ocr_fps,
                    start_seconds=self.start_time,
                    end_seconds=self.end_time,
                )
            elif self.engine_name == "sensevoice":
                import tempfile
                from runtime_paths import models_path
                temp_audio = os.path.join(tempfile.gettempdir(), f"capcap_range_{int(self.start_time * 1000)}_{int(self.end_time * 1000)}.wav")
                ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
                subprocess.run([
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(self.start_time),
                    "-t", str(max(0.1, self.end_time - self.start_time)), "-i", self.video_path,
                    "-vn", "-ac", "1", "-ar", "16000", temp_audio,
                ], check=True, **subprocess_hidden_kwargs())
                model_dir = self.model_path or models_path("sensevoice")
                segments = engine.transcribe_audio_sensevoice(temp_audio, model_dir, language=self.language)
                for segment in segments or []:
                    segment["start"] = round(float(segment.get("start", 0.0)) + self.start_time, 3)
                    segment["end"] = round(float(segment.get("end", 0.0)) + self.start_time, 3)
            else:
                import tempfile
                temp_audio = os.path.join(tempfile.gettempdir(), f"capcap_range_{int(self.start_time * 1000)}_{int(self.end_time * 1000)}.wav")
                ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
                subprocess.run([
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", str(self.start_time),
                    "-t", str(max(0.1, self.end_time - self.start_time)), "-i", self.video_path,
                    "-vn", "-ac", "1", "-ar", "16000", temp_audio,
                ], check=True, **subprocess_hidden_kwargs())
                segments = engine.transcribe_audio(temp_audio, self.model_path, language=self.language)
                for segment in segments or []:
                    segment["start"] = float(segment.get("start", 0.0)) + self.start_time
                    segment["end"] = float(segment.get("end", 0.0)) + self.start_time
            self.completed.emit(list(segments or []), "")

        except Exception as exc:
            self.completed.emit([], str(exc))
        finally:
            if temp_audio:
                try: os.remove(temp_audio)
                except OSError: pass


class TranslationWorker(QThread):
    finished = Signal(str, str, str)

    def __init__(self, srt_text, model_path, src_lang, target_lang, enable_polish):
        super().__init__()
        self.srt_text = srt_text
        self.model_path = model_path
        self.src_lang = src_lang
        self.target_lang = target_lang
        self.enable_polish = enable_polish

    def run(self):
        try:
            try:
                from translation import TranslationOrchestrator
                orch = TranslationOrchestrator()
                provider_type, polisher = orch._resolve_ai_provider()
                print(f"[Translate] Using AI: {orch._describe_ai_provider(provider_type)}")
                result = orch.translate_srt(
                    self.srt_text,
                    src_lang=self.src_lang,
                    target_lang=self.target_lang,
                    enable_polish=self.enable_polish,
                )
                if not result.success:
                    raise RuntimeError("; ".join(result.errors) or "Translation failed.")
                translated_srt = orch.result_to_srt(result)
                fallback_notice = "\n".join(result.warnings or []) if result.used_fallback else ""
            except Exception:
                raise
            self.finished.emit(translated_srt, "", fallback_notice)
        except Exception as exc:
            print(f"Translation Thread Error: {exc}")
            self.finished.emit("", str(exc), "")


class OllamaStatusWorker(QThread):
    """Probe the local Ollama server without blocking the Settings dialog."""
    finished = Signal(bool, str)

    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = str(base_url or "http://localhost:11434/v1")

    def run(self):
        try:
            import requests

            endpoint = self.base_url.rstrip("/")
            if endpoint.endswith("/v1"):
                endpoint = endpoint[:-3]
            response = requests.get(f"{endpoint}/api/tags", timeout=2.5)
            response.raise_for_status()
            self.finished.emit(True, "Connected")
        except Exception as exc:
            self.finished.emit(False, f"Not connected: {exc}")


class OcrTranslatorCaptureWorker(QThread):
    """One-shot OCR capture used by the editor utility, never by ASR."""
    finished = Signal(str, str)

    def __init__(self, video_path, position_seconds, normalized_rect):
        super().__init__()
        self.video_path = video_path
        self.position_seconds = float(position_seconds or 0.0)
        self.normalized_rect = tuple(normalized_rect or ())

    def run(self):
        try:
            from ocr_processor import extract_ocr_text_from_video_region
            text = extract_ocr_text_from_video_region(
                self.video_path, self.position_seconds, self.normalized_rect
            )
            self.finished.emit(text, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class OcrTranslatorTranslationWorker(QThread):
    """Translate a captured OCR value with the configured app provider."""
    finished = Signal(str, str)

    def __init__(self, text, source_lang, target_lang):
        super().__init__()
        self.text = str(text or "")
        self.source_lang = str(source_lang or "auto")
        self.target_lang = str(target_lang or "vi")

    def run(self):
        try:
            engine = EngineRuntime()
            selected_provider = str(os.getenv("OPENAI_PROVIDER") or "google").strip().lower()
            print(f"[OCR Translator] Translating with selected provider: {selected_provider}")
            result = engine.translate_segments(
                [{"start": 0.0, "end": 1.0, "text": self.text}],
                src_lang=self.source_lang,
                target_lang=self.target_lang,
                # This utility must follow the same selected cloud/API
                # provider as the main translation pipeline.  Passing False
                # bypasses it and always takes the Google fallback path.
                enable_polish=True,
                optimize_subtitles=False,
                style_instruction="[mode=ocr_capture]",
            )
            first = (result or [None])[0]
            if isinstance(first, dict):
                translated = first.get("text", "")
                actual_provider = first.get("provider", "")
            else:
                translated = getattr(first, "text", "")
                actual_provider = getattr(first, "provider", "")
            translated = str(translated or "").strip()
            if not translated:
                raise RuntimeError("The translator returned no text.")
            actual_provider = str(actual_provider or selected_provider).strip()
            print(f"[OCR Translator] Translation completed using: {actual_provider}")
            self.finished.emit(translated, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class RewriteTranslationWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, source_segments, translated_segments, src_lang, style_instruction=""):
        super().__init__()
        self.source_segments = source_segments
        self.translated_segments = translated_segments
        self.src_lang = src_lang
        self.style_instruction = style_instruction

    def run(self):
        try:
            engine = EngineRuntime()
            try:
                from translation import TranslationOrchestrator
                orch = TranslationOrchestrator()
                provider_type, polisher = orch._resolve_ai_provider()
                print(f"[Rewrite] Using AI: {orch._describe_ai_provider(provider_type)}")
            except Exception:
                pass
            rewritten_segments = engine.rewrite_translation_segments(
                self.source_segments,
                self.translated_segments,
                src_lang=self.src_lang,
                style_instruction=self.style_instruction,
            )
            from translation.srt_utils import to_srt

            self.finished.emit(to_srt(rewritten_segments), "")
        except Exception as exc:
            print(f"Rewrite Thread Error: {exc}")
            self.finished.emit("", str(exc))


class RuntimeAssetsWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(int, str)  # percent (0-100) or -1 for indeterminate, message

    def __init__(self, workspace_root, whisper_model_name="medium", demucs_model_name="htdemucs"):
        super().__init__()
        self.workspace_root = workspace_root
        self.whisper_model_name = whisper_model_name
        self.demucs_model_name = demucs_model_name

    def run(self):
        try:
            details = []

            self.progress.emit(5, "Checking bundled runtime assets...")
            ffmpeg_path = Path(bin_path("ffmpeg", "ffmpeg.exe"))
            if not ffmpeg_path.exists():
                raise FileNotFoundError(f"Bundled FFmpeg is missing: {ffmpeg_path}")
            details.append(f"FFmpeg ready: {ffmpeg_path}")
            self.progress.emit(12, "FFmpeg is ready.")

            mpv_path = Path(bin_path("mpv", "libmpv-2.dll"))
            if not mpv_path.exists():
                alt_mpv_path = Path(bin_path("mpv", "mpv-2.dll"))
                if not alt_mpv_path.exists():
                    raise FileNotFoundError(f"Bundled libmpv is missing: {mpv_path}")
                mpv_path = alt_mpv_path
            details.append(f"libmpv ready: {mpv_path}")
            self.progress.emit(20, "Preview runtime is ready.")

            from whisper_processor import load_whisper_model

            whisper_cache_dir = Path(self.workspace_root) / "models" / "faster_whisper"
            whisper_cache_dir.mkdir(parents=True, exist_ok=True)
            cached = any(
                p.is_dir() and self.whisper_model_name in p.name.lower()
                for p in whisper_cache_dir.glob("models--*")
            )
            if cached:
                self.progress.emit(35, f"Loading Whisper model: {self.whisper_model_name} ...")
            else:
                self.progress.emit(-1, f"Downloading Whisper model: {self.whisper_model_name} ...")
            load_whisper_model(self.whisper_model_name)
            details.append(f"Whisper model ready: {self.whisper_model_name}")

            self.progress.emit(80, f"Whisper model ready: {self.whisper_model_name}")
            from demucs.pretrained import get_model

            try:
                self.progress.emit(-1, f"Downloading Demucs model: {self.demucs_model_name} ...")
                get_model(self.demucs_model_name)
                details.append(f"Demucs model ready: {self.demucs_model_name}")
            except Exception as demucs_exc:
                warning = f"Demucs preload skipped: {demucs_exc}"
                print(f"RuntimeAssetsWorker Warning: {warning}")
                details.append(warning)

            self.progress.emit(100, "All models ready.")
            self.finished.emit("\n".join(details), "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"RuntimeAssetsWorker Error:\n{details}")
            self.finished.emit("", details or str(exc))


class ResourceDownloadWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(int, str)

    def __init__(self, workspace_root, resource_id):
        super().__init__()
        self.workspace_root = workspace_root
        self.resource_id = resource_id

    def run(self):
        try:
            service = ResourceDownloadService(self.workspace_root)
            service.download_resource(self.resource_id, progress_cb=self.progress.emit)
            self.finished.emit(self.resource_id, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"ResourceDownloadWorker Error:\n{details}")
            self.finished.emit(self.resource_id, details or str(exc))


class TimelineWaveformWorker(QThread):
    finished = Signal(object, object, float, str)

    def __init__(self, request_signature, video_path, audio_path, temp_audio_path):
        super().__init__()
        self.request_signature = request_signature
        self.video_path = str(video_path or "").strip()
        self.audio_path = str(audio_path or "").strip()
        self.temp_audio_path = str(temp_audio_path or "").strip()

    def run(self):
        try:
            audio_path = self.audio_path if self.audio_path and os.path.exists(self.audio_path) else ""
            if not audio_path and self.video_path and os.path.exists(self.video_path):
                temp_audio = self.temp_audio_path
                if temp_audio and not os.path.exists(temp_audio):
                    os.makedirs(os.path.dirname(temp_audio), exist_ok=True)
                    ffmpeg = os.path.join(bin_path("ffmpeg"), "ffmpeg.exe")
                    subprocess.run(
                        [
                            ffmpeg,
                            "-y",
                            "-loglevel",
                            "error",
                            "-i",
                            self.video_path,
                            "-vn",
                            "-acodec",
                            "pcm_s16le",
                            "-ar",
                            "16000",
                            "-ac",
                            "1",
                            temp_audio,
                        ],
                        check=True,
                        timeout=60,
                        **subprocess_hidden_kwargs(),
                    )
                if temp_audio and os.path.exists(temp_audio):
                    audio_path = temp_audio

            if not audio_path or not os.path.exists(audio_path):
                self.finished.emit(self.request_signature, [], 0.0, "")
                return

            from audio_mixer import _require_pydub

            _require_pydub()
            from pydub import AudioSegment
            import numpy as np

            audio = AudioSegment.from_file(audio_path).set_channels(1)
            samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
            duration_s = max(0.0, len(audio) / 1000.0)

            if not samples.size:
                self.finished.emit(self.request_signature, [], duration_s, "")
                return

            samples = samples.astype(np.float32)
            peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            if peak <= 0.0:
                self.finished.emit(self.request_signature, [], duration_s, "")
                return
            samples /= max(1.0, peak)

            # Build a lightweight envelope: fixed number of buckets regardless of video length.
            # This keeps the timeline readable without the FFT cost of a full spectrum view.
            # Keep this compact enough for instant drawing, but retain enough
            # peaks for long source videos to look like a real waveform.
            # These values are computed once in the background, never while
            # playback is running.
            bucket_count = int(min(1200, max(240, round(duration_s * 12.0))))
            chunk_size = max(256, int(np.ceil(samples.size / max(1, bucket_count))))
            waveform = []
            for start in range(0, samples.size, chunk_size):
                chunk = samples[start:start + chunk_size]
                if not chunk.size:
                    waveform.append(0.0)
                    continue
                abs_chunk = np.abs(chunk)
                peak_value = float(np.max(abs_chunk)) if abs_chunk.size else 0.0
                rms_value = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
                value = max(peak_value, rms_value * 1.15)
                waveform.append(min(1.0, max(0.03, value ** 0.85)))

            self.finished.emit(self.request_signature, waveform, duration_s, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.finished.emit(self.request_signature, [], 0.0, details or str(exc))


class TimelineThumbnailWorker(QThread):
    finished = Signal(object, object, str)

    def __init__(self, request_signature, video_path, duration_s, thumb_dir):
        super().__init__()
        self.request_signature = request_signature
        self.video_path = str(video_path or "").strip()
        self.duration_s = max(0.0, float(duration_s or 0.0))
        self.thumb_dir = str(thumb_dir or "").strip()

    def run(self):
        try:
            if not self.video_path or not os.path.exists(self.video_path) or self.duration_s <= 0.0:
                self.finished.emit(self.request_signature, [], "")
                return

            ffmpeg_candidates = [
                bin_path("ffmpeg", "ffmpeg.exe"),
                bin_path("ffmpeg.exe"),
                shutil.which("ffmpeg"),
                shutil.which("ffmpeg.exe"),
            ]
            ffmpeg_path = ""
            for candidate in ffmpeg_candidates:
                if candidate and os.path.isfile(candidate):
                    ffmpeg_path = candidate
                    break

            if not ffmpeg_path:
                self.finished.emit(self.request_signature, [], "")
                return

            # Adapt density to media length: short clips need frequent visual
            # landmarks, while long videos stay bounded for fast preparation.
            if self.duration_s <= 60.0:
                interval_s = max(2.0, self.duration_s / 12.0)
            elif self.duration_s <= 300.0:
                interval_s = max(5.0, self.duration_s / 30.0)
            else:
                interval_s = max(20.0, self.duration_s / 90.0)
            thumb_count = max(1, min(120, int(math.ceil(self.duration_s / interval_s))))
            if self.duration_s <= 1.0:
                timestamps = [0.0]
            else:
                timestamps = [
                    min(self.duration_s - 0.05, max(0.0, idx * interval_s))
                    for idx in range(thumb_count)
                ]

            os.makedirs(self.thumb_dir, exist_ok=True)
            digest = hashlib.md5(
                f"{self.video_path}|{self.request_signature}".encode("utf-8", errors="replace")
            ).hexdigest()[:16]

            startupinfo = None
            creationflags = 0
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

            thumbnails = []
            for idx, timestamp_s in enumerate(timestamps):
                output_path = os.path.join(self.thumb_dir, f"{digest}_{idx:02d}.jpg")
                if not os.path.exists(output_path):
                    cmd = [
                        ffmpeg_path,
                        "-y",
                        "-ss",
                        f"{timestamp_s:.3f}",
                        "-i",
                        self.video_path,
                        "-frames:v",
                        "1",
                        "-q:v",
                        "4",
                        "-vf",
                        "scale=180:-1:force_original_aspect_ratio=decrease",
                        output_path,
                    ]
                    try:
                        subprocess.run(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                            timeout=20,
                            startupinfo=startupinfo,
                            creationflags=creationflags,
                        )
                    except Exception:
                        continue
                if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                    thumbnails.append((float(timestamp_s), output_path))

            self.finished.emit(self.request_signature, thumbnails, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.finished.emit(self.request_signature, [], details or str(exc))


class PrepareWorkflowWorker(QThread):
    finished = Signal(str, str)
    step_started = Signal(str)

    def __init__(
        self,
        workspace_root,
        video_path,
        mode,
        audio_handling_mode,
        source_language,
        target_language,
        translator_ai,
        optimize_subtitles,
        translator_style,
        whisper_model_name,
        transcription_engine="whisper",
        speaker_diarization=False,
        speaker_diarization_num_speakers=-1,
        skip_translation=False,
        prefetch_voice_name="",
        prefetch_voice_speed=1.0,
        remote_api_url="",
        remote_api_token="",
        force_remote_api=False,
    ):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.mode = mode
        self.audio_handling_mode = audio_handling_mode
        self.source_language = source_language
        self.target_language = str(target_language or "vi").strip().lower()
        self.translator_ai = translator_ai
        self.optimize_subtitles = False
        self.translator_style = translator_style
        self.whisper_model_name = whisper_model_name
        self.transcription_engine = transcription_engine
        self.speaker_diarization = bool(speaker_diarization)
        self.speaker_diarization_num_speakers = int(speaker_diarization_num_speakers or -1)
        self.skip_translation = skip_translation
        self.prefetch_voice_name = prefetch_voice_name
        self.prefetch_voice_speed = float(prefetch_voice_speed or 1.0)
        self.remote_api_url = str(remote_api_url or "").strip()
        self.remote_api_token = str(remote_api_token or "").strip()
        self.force_remote_api = bool(force_remote_api)

    def run(self):
        try:
            from runtime_profile import is_remote_profile
            if self.force_remote_api or is_remote_profile():
                from remote_api import remote_api_post
                old_url = os.environ.get("CAPCAP_REMOTE_API_URL")
                old_token = os.environ.get("CAPCAP_REMOTE_API_TOKEN")
                try:
                    if self.remote_api_url:
                        os.environ["CAPCAP_REMOTE_API_URL"] = self.remote_api_url
                    if self.remote_api_token:
                        os.environ["CAPCAP_REMOTE_API_TOKEN"] = self.remote_api_token
                    response = remote_api_post(
                        "/v1/prepare",
                        {
                            "workspace_root": self.workspace_root,
                            "video_path": self.video_path,
                            "source_language": self.source_language,
                            "target_language": self.target_language,
                            "mode": self.mode,
                            "audio_handling_mode": self.audio_handling_mode,
                            "translator_ai": self.translator_ai,
                            "optimize_subtitles": self.optimize_subtitles,
                            "translator_style": self.translator_style,
                            "whisper_model_name": self.whisper_model_name,
                            "transcription_engine": self.transcription_engine,
                            "speaker_diarization": self.speaker_diarization,
                            "speaker_diarization_num_speakers": self.speaker_diarization_num_speakers,
                            "skip_translation": self.skip_translation,
                            "prefetch_voice_name": self.prefetch_voice_name,
                            "prefetch_voice_speed": self.prefetch_voice_speed,
                        },
                        timeout=3600,
                        retries=1 if self.force_remote_api else 3,
                    )
                    self.finished.emit(str(response.get("project_state_path", "")), "")
                finally:
                    if old_url is None:
                        os.environ.pop("CAPCAP_REMOTE_API_URL", None)
                    else:
                        os.environ["CAPCAP_REMOTE_API_URL"] = old_url
                    if old_token is None:
                        os.environ.pop("CAPCAP_REMOTE_API_TOKEN", None)
                    else:
                        os.environ["CAPCAP_REMOTE_API_TOKEN"] = old_token
            else:
                from workflows.prepare_workflow import PrepareWorkflow
                workflow = PrepareWorkflow(self.workspace_root)
                project_state = workflow.run(
                    video_path=self.video_path,
                    source_language=self.source_language,
                    target_language=self.target_language,
                    mode=self.mode,
                    audio_handling_mode=self.audio_handling_mode,
                    translator_ai=self.translator_ai,
                    optimize_subtitles=self.optimize_subtitles,
                    translator_style=self.translator_style,
                    whisper_model_name=self.whisper_model_name,
                    transcription_engine=self.transcription_engine,
                    speaker_diarization=self.speaker_diarization,
                    speaker_diarization_num_speakers=self.speaker_diarization_num_speakers,
                    skip_translation=self.skip_translation,
                    prefetch_voice_name=self.prefetch_voice_name,
                    prefetch_voice_speed=self.prefetch_voice_speed,
                    step_callback=self.step_started.emit,
                )
                state_path = os.path.join(project_state.project_root, "project.json")
                self.finished.emit(state_path, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class VoiceOverWorker(QThread):
    finished = Signal(str, str, object, str)
    progress = Signal(str)  # New signal for progress messages

    def __init__(self, workspace_root, segments, output_dir, background_path, audio_handling_mode, voice_name, voice_speed, timing_sync_mode, original_volume, dub_volume, project_state_path="", project_temp_dir="", ai_rewrite_dubbing=False, dubbing_style_instruction="", source_language="auto"):
        super().__init__()
        self.workspace_root = workspace_root
        self.segments = segments
        self.output_dir = output_dir
        self.background_path = background_path
        self.audio_handling_mode = audio_handling_mode
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.timing_sync_mode = timing_sync_mode
        self.original_volume = original_volume
        self.dub_volume = dub_volume
        self.project_state_path = project_state_path
        self.project_temp_dir = project_temp_dir
        self.ai_rewrite_dubbing = ai_rewrite_dubbing
        self.dubbing_style_instruction = dubbing_style_instruction
        self.source_language = source_language

    def run(self):
        try:
            from runtime_profile import is_remote_profile
            print(f"[VoiceOverWorker DEBUG] Starting with voice_name='{self.voice_name}'")
            self.progress.emit(f"[VoiceOverWorker DEBUG] voice_name='{self.voice_name}'")

            if is_remote_profile():
                from remote_api import remote_api_post
                response = remote_api_post(
                    "/v1/voice",
                    {
                        "workspace_root": self.workspace_root,
                        "segments": self.segments,
                        "output_dir": self.output_dir,
                        "background_path": self.background_path,
                        "audio_handling_mode": self.audio_handling_mode,
                        "voice_name": self.voice_name,
                        "voice_speed": self.voice_speed,
                        "timing_sync_mode": self.timing_sync_mode,
                        "original_volume": self.original_volume,
                        "dub_volume": self.dub_volume,
                        "project_state_path": self.project_state_path,
                        "project_temp_dir": self.project_temp_dir,
                        "ai_rewrite_dubbing": self.ai_rewrite_dubbing,
                        "dubbing_style_instruction": self.dubbing_style_instruction,
                        "source_language": self.source_language,
                    },
                    timeout=3600,
                )
                result = response.get("result", {})
                self.finished.emit(
                    result.get("voice_track", ""),
                    result.get("mixed_path", ""),
                    result.get("segments", []),
                    "",
                )
            else:
                from workflows.voice_workflow import VoiceWorkflow
                workflow = VoiceWorkflow(self.workspace_root)
                result = workflow.run(
                    segments=self.segments,
                    output_dir=self.output_dir,
                    background_path=self.background_path,
                    audio_handling_mode=self.audio_handling_mode,
                    voice_name=self.voice_name,
                    voice_speed=float(self.voice_speed),
                    timing_sync_mode=self.timing_sync_mode,
                    original_volume=int(self.original_volume),
                    dub_volume=int(self.dub_volume),
                    project_state_path=self.project_state_path,
                    project_temp_dir=self.project_temp_dir,
                    ai_rewrite_dubbing=self.ai_rewrite_dubbing,
                    dubbing_style_instruction=self.dubbing_style_instruction,
                    source_language=self.source_language,
                    on_progress=self.progress.emit,
                )
                self.finished.emit(
                    result.get("voice_track", ""),
                    result.get("mixed_path", ""),
                    result.get("segments", []),
                    "",
                )
        except Exception as exc:
            print(f"[VoiceOverWorker ERROR] {str(exc)}")
            self.finished.emit("", "", [], str(exc))


class FinalExportWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(int, str)

    def __init__(self, workspace_root, video_path, output_path, mode, srt_path="", ass_path="", audio_path="", subtitle_style=None, output_quality="source", output_fps="source", output_ratio="source", output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, video_filter_state=None, original_audio_gain_db=0.0, project_state_path="", project_temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.ass_path = ass_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}
        self.output_quality = output_quality
        self.output_fps = output_fps
        self.output_ratio = output_ratio
        self.output_scale_mode = output_scale_mode
        self.output_fill_focus_x = output_fill_focus_x
        self.output_fill_focus_y = output_fill_focus_y
        self.video_filter_state = video_filter_state or {}
        self.original_audio_gain_db = float(original_audio_gain_db or 0.0)
        self.project_state_path = project_state_path
        self.project_temp_dir = project_temp_dir

    def run(self):
        try:
            from runtime_profile import is_remote_profile
            self.progress.emit(0, "Sending export request to backend...")
            if is_remote_profile():
                from remote_api import remote_api_post
                response = remote_api_post(
                    "/v1/export",
                    {
                        "workspace_root": self.workspace_root,
                        "video_path": self.video_path,
                        "output_path": self.output_path,
                        "mode": self.mode,
                        "srt_path": self.srt_path,
                        "ass_path": self.ass_path,
                        "audio_path": self.audio_path,
                        "subtitle_style": self.subtitle_style,
                        "output_quality": self.output_quality,
                        "output_fps": self.output_fps,
                        "output_ratio": self.output_ratio,
                        "output_scale_mode": self.output_scale_mode,
                        "output_fill_focus_x": self.output_fill_focus_x,
                        "output_fill_focus_y": self.output_fill_focus_y,
                        "video_filter_state": self.video_filter_state,
                        "original_audio_gain_db": self.original_audio_gain_db,
                        "project_state_path": self.project_state_path,
                        "project_temp_dir": self.project_temp_dir,
                    },
                    timeout=3600,
                )
                self.progress.emit(100, "Export complete.")
                self.finished.emit(str(response.get("output_path", "")), "")
            else:
                from workflows.export_workflow import ExportWorkflow
                workflow = ExportWorkflow(self.workspace_root)
                output_path = workflow.run(
                    video_path=self.video_path,
                    output_path=self.output_path,
                    mode=self.mode,
                    srt_path=self.srt_path,
                    ass_path=self.ass_path,
                    audio_path=self.audio_path,
                    subtitle_style=self.subtitle_style,
                    output_quality=self.output_quality,
                    output_fps=self.output_fps,
                    output_ratio=self.output_ratio,
                    output_scale_mode=self.output_scale_mode,
                    output_fill_focus_x=self.output_fill_focus_x,
                    output_fill_focus_y=self.output_fill_focus_y,
                    video_filter_state=self.video_filter_state,
                    original_audio_gain_db=self.original_audio_gain_db,
                    project_state_path=self.project_state_path,
                    project_temp_dir=self.project_temp_dir,
                    on_progress=self.progress.emit,
                )
                self.finished.emit(output_path, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class SegmentAudioPreviewWorker(QThread):
    finished = Signal(int, str, str)

    def __init__(self, workspace_root, index, text, voice_name, voice_speed, temp_dir="", cache_temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.index = index
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.temp_dir = temp_dir
        self.cache_temp_dir = cache_temp_dir

    def run(self):
        try:
            engine = EngineRuntime()
            preview_temp_dir = self.temp_dir or os.path.join(self.workspace_root, "temp", "segment_audio_preview")
            os.makedirs(preview_temp_dir, exist_ok=True)
            cache_temp_dir = self.cache_temp_dir or preview_temp_dir
            os.makedirs(cache_temp_dir, exist_ok=True)

            import importlib.util
            _vpu_path = os.path.join(APP_PATH, "utils", "voice_preview_utils.py")
            _vpu_spec = importlib.util.spec_from_file_location("_voice_preview_utils", _vpu_path)
            _vpu = importlib.util.module_from_spec(_vpu_spec)
            _vpu_spec.loader.exec_module(_vpu)
            clamp_requested_speed = _vpu.clamp_requested_speed
            load_manifest = _vpu.load_manifest
            provider_native_speed = _vpu.provider_native_speed
            save_manifest = _vpu.save_manifest
            segment_cache_key = _vpu.segment_cache_key
            voice_provider = _vpu.voice_provider

            requested_speed = clamp_requested_speed(float(self.voice_speed))
            v_provider = voice_provider(self.voice_name)
            provider_speed = provider_native_speed(
                provider=v_provider,
                requested_speed=requested_speed,
            )
            residual_speed = (requested_speed / provider_speed) if provider_speed > 0.0 else requested_speed

            base_wav_path = os.path.join(cache_temp_dir, f"seg_{self.index:04d}_base.wav")
            engine.synthesize_segment(
                text=self.text,
                wav_path=base_wav_path,
                voice=self.voice_name,
                speed=provider_speed,
                tmp_dir=cache_temp_dir,
            )

            manifest = load_manifest(cache_temp_dir)
            manifest_segments = dict(manifest.get("segments", {}) or {})
            manifest_by_cache_key = dict(manifest.get("by_cache_key", {}) or {})
            cache_key = segment_cache_key(
                text=self.text,
                voice_name=self.voice_name,
                provider_speed=provider_speed,
            )
            manifest_entry = {
                "cache_key": cache_key,
                "wav_path": base_wav_path,
                "text": self.text,
                "voice_name": self.voice_name,
                "provider_speed": provider_speed,
            }
            manifest_segments[str(self.index)] = manifest_entry
            manifest_by_cache_key[cache_key] = dict(manifest_entry)
            manifest["segments"] = manifest_segments
            manifest["by_cache_key"] = manifest_by_cache_key
            save_manifest(cache_temp_dir, manifest)

            wav_path = os.path.join(preview_temp_dir, f"segment_{self.index}_{os.getpid()}.wav")
            if abs(residual_speed - 1.0) >= 0.02:
                output = engine.change_wav_speed(
                    input_wav_path=base_wav_path,
                    output_wav_path=wav_path,
                    speed_ratio=residual_speed,
                )
            else:
                output = base_wav_path
            self.finished.emit(self.index, output, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.finished.emit(self.index, "", details or str(exc))


class VoiceSamplePreviewWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(str)

    def __init__(self, workspace_root, text, voice_name, voice_speed, temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.temp_dir = temp_dir

    def run(self):
        try:
            temp_dir = self.temp_dir or os.path.join(self.workspace_root, "temp", "voice_sample_preview")
            os.makedirs(temp_dir, exist_ok=True)
            cache_seed = f"{self.voice_name}|{self.voice_speed}|{self.text}".encode("utf-8", errors="replace")
            cache_key = hashlib.sha1(cache_seed).hexdigest()[:16]
            wav_path = os.path.join(temp_dir, f"voice_sample_{cache_key}.wav")
            base_wav_path = os.path.join(temp_dir, f"voice_sample_{cache_key}_base.wav")
            if abs(float(self.voice_speed) - 1.0) >= 0.02:
                if self._is_preview_audio_usable(wav_path):
                    self.finished.emit(self._normalize_preview_wav(wav_path, temp_dir=temp_dir), "")
                    return
                self._remove_if_exists(wav_path)
            elif os.path.exists(base_wav_path):
                if self._is_preview_audio_usable(base_wav_path):
                    self.finished.emit(self._normalize_preview_wav(base_wav_path, temp_dir=temp_dir), "")
                    return
                self._remove_if_exists(base_wav_path)
            engine = EngineRuntime()
            staging_base_wav_path = os.path.join(temp_dir, f"voice_sample_{cache_key}_base_work.wav")
            self._remove_if_exists(staging_base_wav_path)
            engine.synthesize_segment(
                text=self.text,
                wav_path=staging_base_wav_path,
                voice=self.voice_name,
                speed=1.0,
                tmp_dir=temp_dir,
                on_progress=self.progress.emit,
            )
            if not self._is_preview_audio_usable(staging_base_wav_path):
                raise RuntimeError("Generated voice preview audio is empty or invalid.")
            os.replace(staging_base_wav_path, base_wav_path)
            speed_value = float(self.voice_speed)
            if abs(speed_value - 1.0) >= 0.02:
                self._remove_if_exists(wav_path)
                output = engine.change_wav_speed(
                    input_wav_path=base_wav_path,
                    output_wav_path=wav_path,
                    speed_ratio=speed_value,
                )
            else:
                output = base_wav_path
            if not self._is_preview_audio_usable(output):
                raise RuntimeError("Voice preview audio could not be prepared for playback.")
            output = self._normalize_preview_wav(output, temp_dir=temp_dir)
            if not self._is_preview_audio_usable(output):
                raise RuntimeError("Normalized voice preview audio is empty or invalid.")
            self.finished.emit(output, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            self.finished.emit("", details or str(exc))

    def _normalize_preview_wav(self, wav_path: str, *, temp_dir: str) -> str:
        candidate = str(wav_path or "").strip()
        if not candidate or not os.path.exists(candidate):
            return candidate
        try:
            normalized_path = os.path.join(temp_dir, f"{Path(candidate).stem}_normalized.wav")
            ffmpeg_path = Path(bin_path("ffmpeg", "ffmpeg.exe"))
            if ffmpeg_path.exists():
                cmd = [
                    str(ffmpeg_path),
                    "-y",
                    "-loglevel",
                    "error",
                    "-i",
                    candidate,
                    "-vn",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    normalized_path,
                ]
                proc = subprocess.run(
                    cmd, capture_output=True, text=True,
                    **subprocess_hidden_kwargs(),
                )
                if proc.returncode == 0 and self._is_preview_audio_usable(normalized_path):
                    return normalized_path
        except Exception:
            pass
        return candidate

    def _is_preview_audio_usable(self, audio_path: str) -> bool:
        candidate = str(audio_path or "").strip()
        if not candidate or not os.path.exists(candidate):
            return False
        if os.path.getsize(candidate) <= 44:
            return False
        ffprobe_path = Path(bin_path("ffmpeg", "ffprobe.exe"))
        if not ffprobe_path.exists():
            return True
        try:
            proc = subprocess.run(
                [str(ffprobe_path), "-v", "error", "-show_streams", candidate],
                capture_output=True,
                text=True,
                **subprocess_hidden_kwargs(),
            )
            return proc.returncode == 0 and bool((proc.stdout or "").strip())
        except Exception:
            return False

    def _remove_if_exists(self, path: str) -> None:
        candidate = str(path or "").strip()
        if not candidate:
            return
        try:
            if os.path.exists(candidate):
                os.remove(candidate)
        except Exception:
            pass



