"""Local, headless MCP entry point for CapCap workflows."""

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
from threading import Lock
from typing import Any, Literal
from uuid import uuid4

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field, model_validator
from typing_extensions import TypedDict

from mcp_worker import EVENT_PREFIX
from mcp_assets import job_provenance, publish_assets
from runtime_paths import subprocess_hidden_kwargs


SOURCE_ROOT = Path(__file__).resolve().parents[1]
WORKER = Path(__file__).with_name("mcp_worker.py").resolve()
AsrEngine = Literal["whisper", "sensevoice", "ocr", "capcut"]
ExportMode = Literal["subtitle", "voice", "both"]
OcrBackend = Literal["rapidocr", "winocr"]
CapCutBackend = Literal["web", "pc"]
TranslationProvider = Literal[
    "google", "bing", "google_ai_studio", "gemini", "openai", "ollama",
    "browser_ai", "gemini_web", "chatgpt_web",
]


class OcrRegion(BaseModel):
    """Rectangle in fractions of the original video frame, measured from the top left."""

    x: float = Field(ge=0, lt=1, description="Left edge as a fraction of frame width")
    y: float = Field(ge=0, lt=1, description="Top edge as a fraction of frame height")
    width: float = Field(gt=0, le=1, description="Width as a fraction of frame width")
    height: float = Field(gt=0, le=1, description="Height as a fraction of frame height")

    @model_validator(mode="after")
    def within_frame(self):
        if self.x + self.width > 1 or self.y + self.height > 1:
            raise ValueError("The OCR region must fit inside the video frame.")
        return self


class JobStatus(TypedDict):
    job_id: str
    operation: str
    state: Literal["queued", "running", "cancelling", "cancelled", "completed", "failed"]
    phase: str
    progress: int | None
    result: dict[str, Any] | None
    error: str | None
    log_path: str


@dataclass
class Job:
    id: str
    operation: str
    directory: Path
    provenance: dict
    state: str = "queued"
    phase: str = "queued"
    progress: int | None = None
    result: dict | None = None
    error: str | None = None
    process: subprocess.Popen | None = None

    def snapshot(self) -> JobStatus:
        return {
            "job_id": self.id,
            "operation": self.operation,
            "state": self.state,
            "phase": self.phase,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "log_path": str(self.directory / "job.log"),
        }


def _stop_worker(process):
    if process is None or process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True, timeout=5, **subprocess_hidden_kwargs(),
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
        if process.poll() is None:
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class JobQueue:
    """Keep each invocation separate and run one media worker at a time."""

    def __init__(self, data_dir):
        self.directory = Path(data_dir).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="capcap-mcp")
        self._closing = False

    def submit(self, operation, **options):
        with self._lock:
            if self._closing:
                raise RuntimeError("CapCap MCP is shutting down.")
            job_id = uuid4().hex
            directory = self.directory / job_id
            directory.mkdir()
            provenance = job_provenance(options, self.directory)
            job = Job(job_id, operation, directory, provenance)
            request = directory / "request.json"
            request.write_text(json.dumps({
                "operation": operation, "options": options, "directory": str(directory),
            }), encoding="utf-8")
            self._jobs[job_id] = job
            self._executor.submit(self._run, job, request)
            return job.snapshot()

    def _find(self, job_id):
        try:
            return self._jobs[job_id]
        except KeyError:
            raise ValueError("Unknown job ID for this CapCap MCP session.") from None

    def status(self, job_id):
        with self._lock:
            return self._find(job_id).snapshot()

    def cancel(self, job_id):
        with self._lock:
            job = self._find(job_id)
            if job.state == "queued":
                job.state = job.phase = "cancelled"
            elif job.state == "running":
                job.state = "cancelling"
            process = job.process if job.state == "cancelling" else None
        _stop_worker(process)
        return self.status(job_id)

    def close(self):
        with self._lock:
            self._closing = True
            for job in self._jobs.values():
                if job.state == "queued":
                    job.state = job.phase = "cancelled"
                elif job.state == "running":
                    job.state = "cancelling"
            active = [job.process for job in self._jobs.values() if job.state == "cancelling"]
        for process in active:
            _stop_worker(process)
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, job, request):
        process = None
        result = None
        error = None
        code = None
        try:
            with self._lock:
                if job.state != "queued":
                    return
                with (job.directory / "job.log").open("w", encoding="utf-8") as log:
                    process = subprocess.Popen(
                        [sys.executable, str(WORKER), str(request)], cwd=SOURCE_ROOT,
                        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=log,
                        text=True, encoding="utf-8", errors="replace",
                        env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8"),
                        start_new_session=os.name != "nt", **subprocess_hidden_kwargs(),
                    )
                job.process = process
                job.state = "running"
                job.phase = "starting"
            with process.stdout as events:
                for line in events:
                    if not line.startswith(EVENT_PREFIX):
                        continue
                    event = json.loads(line[len(EVENT_PREFIX):])
                    if "phase" in event:
                        with self._lock:
                            if job.phase != event["phase"]:
                                job.progress = None
                            job.phase = event["phase"]
                            if event.get("progress") is not None:
                                job.progress = max(0, min(100, int(event["progress"])))
                    elif "result" in event:
                        result = event["result"]
                    elif "error" in event:
                        error = event["error"]
            code = process.wait()
        except (OSError, ValueError) as exc:
            error = type(exc).__name__
        finally:
            _stop_worker(process)
            if process is not None:
                process.wait()
            with self._lock:
                job.process = None
                if job.state in {"cancelling", "cancelled"}:
                    job.state = job.phase = "cancelled"
                    job.progress = None
                elif code == 0 and result is not None:
                    if job.operation != "inspect":
                        try:
                            publish_assets(job.directory, job.provenance, result.get("assets"))
                        except OSError as exc:
                            result["asset_index_warning"] = str(exc)
                    job.state = job.phase = "completed"
                    job.progress = 100
                    job.result = result
                else:
                    job.state = job.phase = "failed"
                    job.progress = None
                    job.error = f"{error or 'Worker exited early'}. See the local job log."


def _input_file(value, label):
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError(f"{label} must be an absolute path to an existing file on the CapCap machine.")
    return str(path.resolve())


def _ocr_region_option(engine, backend, region):
    if engine != "ocr" and (backend is not None or region is not None):
        raise ValueError("Choose transcription_engine='ocr' to set an OCR backend or region.")
    return region.model_dump() if region is not None else None


def _use_ai_translation(provider, use_ai):
    if provider in {"google", "bing"}:
        return False
    if provider is not None and not use_ai:
        raise ValueError("Set use_ai=true to use the selected AI translation provider.")
    return use_ai


def create_server(jobs):
    server = FastMCP("CapCap", log_level="WARNING", instructions=(
        "Submit a CapCap task with local absolute file paths. Use capcap_job_status to poll "
        "the returned job_id. Completed jobs return local artifact paths. Progress is per phase."
    ))

    @server.tool()
    def capcap_inspect_video(video_path: str) -> JobStatus:
        """Inspect local video streams and reusable assets for the same source video."""
        return jobs.submit("inspect", video_path=_input_file(video_path, "video_path"))

    @server.tool()
    def capcap_list_resources(language: str | None = None) -> JobStatus:
        """List local model installation status and voice IDs, optionally filtered by language."""
        return jobs.submit("resources", language=language)

    @server.tool()
    def capcap_extract_audio(video_path: str) -> JobStatus:
        """Extract original video audio as WAV."""
        return jobs.submit("extract_audio", video_path=_input_file(video_path, "video_path"))

    @server.tool()
    def capcap_extract_subtitles(video_path: str, stream_index: int) -> JobStatus:
        """Extract an embedded text subtitle stream as SRT. Get stream indices from capcap_inspect_video."""
        if stream_index < 0:
            raise ValueError("Set stream_index to the subtitle stream index returned by capcap_inspect_video.")
        return jobs.submit("extract_subtitles", video_path=_input_file(video_path, "video_path"),
                           stream_index=stream_index)

    @server.tool()
    def capcap_separate_audio(audio_path: str, video_path: str | None = None) -> JobStatus:
        """Split local audio into vocals and background music as separate WAV files."""
        return jobs.submit("separate_audio", audio_path=_input_file(audio_path, "audio_path"),
                           video_path=_input_file(video_path, "video_path") if video_path else None)

    @server.tool()
    def capcap_transcribe(video_path: str, source_language: str = "auto",
                          transcription_engine: AsrEngine = "whisper", whisper_model_name: str = "base",
                          ocr_backend: OcrBackend | None = None, ocr_region: OcrRegion | None = None,
                          capcut_backend: CapCutBackend | None = None) -> JobStatus:
        """Transcribe a video to SRT. OCR accepts a normalized rectangle and backend. CapCut accepts Web or PC."""
        if capcut_backend is not None and transcription_engine != "capcut":
            raise ValueError("Choose transcription_engine='capcut' to set capcut_backend.")
        return jobs.submit("transcribe", video_path=_input_file(video_path, "video_path"),
                           source_language=source_language, transcription_engine=transcription_engine,
                           whisper_model_name=whisper_model_name, ocr_backend=ocr_backend,
                           ocr_region=_ocr_region_option(transcription_engine, ocr_backend, ocr_region),
                           capcut_backend=capcut_backend)

    @server.tool()
    def capcap_translate(srt_path: str, target_language: str, source_language: str = "auto",
                         use_ai: bool = True, provider: TranslationProvider | None = None,
                         video_path: str | None = None) -> JobStatus:
        """Translate an SRT with the configured service or a selected Google, Bing, or AI provider."""
        return jobs.submit("translate", srt_path=_input_file(srt_path, "srt_path"),
                           source_language=source_language, target_language=target_language,
                           use_ai=_use_ai_translation(provider, use_ai), provider=provider,
                           video_path=_input_file(video_path, "video_path") if video_path else None)

    @server.tool()
    def capcap_generate_voice(srt_path: str, voice_name: str, language: str = "vi", voice_speed: float = 1.0,
                               video_path: str | None = None) -> JobStatus:
        """Generate a dubbed WAV from a local SRT and a CapCap voice ID such as piper:banmai."""
        if not voice_name.strip() or not 0.0 < voice_speed <= 4.0:
            raise ValueError("Set a valid CapCap voice ID and a voice speed in (0, 4].")
        return jobs.submit("voice", srt_path=_input_file(srt_path, "srt_path"),
                           voice_name=voice_name, language=language, voice_speed=voice_speed,
                           video_path=_input_file(video_path, "video_path") if video_path else None)

    @server.tool()
    def capcap_export(video_path: str, srt_path: str = "", audio_path: str = "",
                      output_ratio: str = "source",
                      audio_quality: Literal["economy", "standard", "high"] = "standard") -> JobStatus:
        """Export a local video to MP4 with an SRT, dubbed audio, or both."""
        if not srt_path and not audio_path:
            raise ValueError("Provide an SRT file or a dubbed audio file.")
        mode = "both" if srt_path and audio_path else "subtitle" if srt_path else "voice"
        return jobs.submit("export", video_path=_input_file(video_path, "video_path"),
                           srt_path=_input_file(srt_path, "srt_path") if srt_path else "",
                           audio_path=_input_file(audio_path, "audio_path") if audio_path else "",
                           mode=mode, output_ratio=output_ratio, audio_quality=audio_quality)

    @server.tool()
    def capcap_process_video(video_path: str, target_language: str = "vi", source_language: str = "auto",
                             mode: ExportMode = "subtitle", voice_name: str = "piper:banmai",
                             transcription_engine: AsrEngine = "whisper", whisper_model_name: str = "base",
                             skip_translation: bool = False, use_ai: bool = True,
                             output_ratio: str = "source", ocr_backend: OcrBackend | None = None,
                             ocr_region: OcrRegion | None = None, capcut_backend: CapCutBackend | None = None,
                             translation_provider: TranslationProvider | None = None) -> JobStatus:
        """Run the CapCap workflow from local video to MP4. Voice modes also generate dubbing."""
        uses_capcut_voice = mode in {"voice", "both"} and voice_name.startswith("capcut:")
        if capcut_backend is not None and transcription_engine != "capcut" and not uses_capcut_voice:
            raise ValueError("Choose CapCut transcription or a CapCut voice to set capcut_backend.")
        return jobs.submit("process_video", video_path=_input_file(video_path, "video_path"),
                           target_language=target_language, source_language=source_language, mode=mode,
                           voice_name=voice_name, transcription_engine=transcription_engine,
                           whisper_model_name=whisper_model_name, skip_translation=skip_translation,
                           use_ai=use_ai if skip_translation else _use_ai_translation(translation_provider, use_ai),
                           output_ratio=output_ratio, ocr_backend=ocr_backend,
                           ocr_region=_ocr_region_option(transcription_engine, ocr_backend, ocr_region),
                           capcut_backend=capcut_backend,
                           provider=translation_provider if not skip_translation else None)

    @server.tool()
    def capcap_job_status(job_id: str) -> JobStatus:
        """Return the state, phase, progress, and eventual output files for a CapCap job."""
        return jobs.status(job_id)

    @server.tool()
    async def capcap_cancel_job(job_id: str) -> JobStatus:
        """Cancel a queued or running CapCap job."""
        return await asyncio.to_thread(jobs.cancel, job_id)

    return server


def main():
    data_root = Path(os.getenv("LOCALAPPDATA") or tempfile.gettempdir()) / "CapCap" / "mcp"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=data_root, help="Directory for MCP job files and outputs")
    args = parser.parse_args()
    jobs = JobQueue(args.data_dir)
    try:
        create_server(jobs).run(transport="stdio")
    finally:
        jobs.close()


if __name__ == "__main__":
    main()
