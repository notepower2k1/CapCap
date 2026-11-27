"""Run one CapCap MCP job in an isolated subprocess."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback
from contextlib import nullcontext


EVENT_PREFIX = "CAPCAP_MCP "


def _apply_job_environment(options):
    """Apply request overrides inside this job's subprocess."""
    for option, variable in (
        ("ocr_backend", "OCR_BACKEND"),
        ("capcut_backend", "CAPCUT_BACKEND"),
        ("provider", "OPENAI_PROVIDER"),
        ("provider", "AI_POLISHER_PROVIDER"),
    ):
        if options.get(option) is not None:
            os.environ[variable] = options[option]
    region = options.get("ocr_region")
    if region is not None:
        os.environ["OCR_SUBTITLE_RECT"] = ",".join(
            str(region[key]) for key in ("x", "y", "width", "height")
        )


def run_job(operation, options, directory, report):
    from mcp_assets import asset, inspect_video, list_resources, probe_video, project_assets
    from runtime_paths import bin_path, subprocess_hidden_kwargs
    from services.workflow_runtime import WorkflowRuntime
    from translation.srt_utils import parse_srt, to_srt

    directory = Path(directory)

    def read_cues(path):
        rows = parse_srt(Path(path).read_text(encoding="utf-8-sig"))
        if not rows:
            raise ValueError("The SRT file contains no subtitle cues.")
        return rows

    def result_file(path):
        if not path or not Path(path).is_file() or not Path(path).stat().st_size:
            raise RuntimeError("The workflow did not produce its output file.")
        return str(Path(path).resolve())

    def voice_progress(message):
        match = re.search(r"\((\d+(?:\.\d+)?)%\)", str(message))
        report("voice", float(match[1]) if match else None)

    if operation == "inspect":
        report("inspect")
        return inspect_video(options["video_path"], directory.parent)

    if operation == "resources":
        report("resources")
        return list_resources(options.get("language"))

    if operation == "extract_audio":
        from video_processor import extract_audio

        report("extract_audio")
        output = directory / "original.wav"
        if not extract_audio(options["video_path"], str(output)):
            raise RuntimeError("Audio extraction failed. Check the input has an audio track.")
        record = asset("original_audio", output)
        return {"audio_path": record["path"], "assets": [record]}

    if operation == "extract_subtitles":
        report("extract_subtitles")
        video = options["video_path"]
        index = options["stream_index"]
        track = next((item for item in probe_video(video)["streams"]
                      if item["type"] == "subtitle" and item["index"] == index), None)
        if not track or not track["can_extract_srt"]:
            raise ValueError("Choose an embedded text subtitle stream returned by capcap_inspect_video.")
        output = directory / "embedded.srt"
        subprocess.run([
            bin_path("ffmpeg", "ffmpeg.exe"), "-v", "error", "-nostdin", "-i", video,
            "-map", f"0:{index}", "-c:s", "srt", str(output),
        ], check=True, **subprocess_hidden_kwargs())
        record = asset("embedded_subtitles", output, language=track["language"], cue_count=len(read_cues(output)))
        return {"srt_path": record["path"], "assets": [record]}

    if operation == "separate_audio":
        from vocal_processor import separate_vocals

        report("separation")
        vocals, music = separate_vocals(options["audio_path"], str(directory))
        vocals_record = asset("vocals", vocals)
        music_record = asset("music", music)
        return {"vocals_path": vocals_record["path"], "music_path": music_record["path"],
                "assets": [vocals_record, music_record]}

    if operation == "translate":
        from translator import translate_segments

        rows = read_cues(options["srt_path"])
        report("translation", 0)
        completed_indices = set()

        def translated_batch(start, batch):
            completed_indices.update(range(start, start + len(batch)))
            report("translation", 100 * len(completed_indices) // len(rows))

        translated = translate_segments(
            rows, src_lang=options["source_language"], target_lang=options["target_language"],
            enable_polish=options["use_ai"], override_provider=options["provider"],
            batch_callback=translated_batch,
        )
        if len(translated) != len(rows):
            raise RuntimeError("Translation returned a different number of subtitle cues.")
        output = directory / "translated.srt"
        output.write_text(to_srt(translated, max_gap_ms=0), encoding="utf-8")
        providers = sorted({row["provider"] for row in translated if row.get("provider")})
        record = asset("translated_subtitles", output, language=options["target_language"],
                       requested_provider=options["provider"], providers=providers, cue_count=len(translated))
        return {"srt_path": record["path"], "cue_count": len(translated),
                "source_language": options["source_language"], "target_language": options["target_language"],
                "providers": providers, "assets": [record]}

    if operation in {"transcribe", "process_video"}:
        video = options["video_path"]
        prepare = {
            key: options[key]
            for key in ("source_language", "target_language", "transcription_engine", "whisper_model_name")
            if key in options
        }
        if prepare["transcription_engine"] == "whisper":
            from whisper_processor import capture_detected_languages
            detection = capture_detected_languages()
        else:
            detection = nullcontext(set())
        if operation == "process_video":
            from queue_worker import run_job as run_pipeline

            prepare.update(mode=options["mode"], skip_translation=options["skip_translation"],
                           translator_ai=options["use_ai"], audio_handling_mode="fast")
            config = {
                "prepare": prepare,
                "voice": {"voice_name": options["voice_name"], "target_language": options["target_language"],
                          "source_language": options["source_language"]},
                "export": {"output_ratio": options["output_ratio"]},
            }
            with detection as languages:
                result = run_pipeline(str(directory), video, config, on_step=report)
            project_path = result_file(result["project"])
            detected = next(iter(languages)) if len(languages) == 1 else None
            records = project_assets(project_path)
            for record in records:
                kind = record["kind"]
                if kind in {"transcript", "translated_subtitles"}:
                    cues = parse_srt(Path(record["path"]).read_text(encoding="utf-8-sig"))
                    record["cue_count"] = len(cues)
                if kind == "transcript" and detected:
                    record["language"] = detected
                if kind == "dubbed_audio":
                    record["voice_name"] = options["voice_name"]
            output = asset("exported_video", result["output"])
            return {"output_path": output["path"], "project_path": project_path,
                    "source_language": options["source_language"], "target_language": options["target_language"],
                    "detected_language": detected, "assets": [*records, output]}

        runtime = WorkflowRuntime(str(directory))
        with detection as languages:
            state = runtime.run_prepare(
                video, mode="subtitle", skip_translation=True, translator_ai=False,
                step_callback=lambda phase, message="", percent=None: report(phase, percent),
                **prepare,
            )
        project_path = state if isinstance(state, (str, Path)) else runtime.project_state_path(state)
        project = runtime.project_service.load_project(str(project_path))
        srt = result_file(project.artifacts.get("subtitle_original_srt"))
        detected = next(iter(languages)) if len(languages) == 1 else None
        records = project_assets(project_path)
        transcript = next(record for record in records if record["kind"] == "transcript")
        if detected:
            transcript["language"] = detected
        transcript["cue_count"] = len(read_cues(srt))
        return {"srt_path": srt, "project_path": result_file(project_path), "assets": records,
                "source_language": options["source_language"], "detected_language": detected,
                "cue_count": transcript["cue_count"]}

    runtime = WorkflowRuntime(str(directory))
    if operation == "voice":
        rows = read_cues(options["srt_path"])
        report("voice", 0)
        result = runtime.voice_workflow.run(
            segments=rows, output_dir=str(directory), project_temp_dir=str(directory / "segments"),
            voice_name=options["voice_name"], voice_speed=options["voice_speed"],
            target_language=options["language"], on_progress=voice_progress,
        )
        record = asset("dubbed_audio", result["voice_track"], language=options["language"],
                       voice_name=options["voice_name"], cue_count=len(rows))
        return {"audio_path": record["path"], "cue_count": len(rows), "assets": [record]}

    if operation == "export":
        srt_path = ""
        if options["srt_path"]:
            # ASS generation writes alongside its input SRT.
            srt_path = str(directory / "export.srt")
            shutil.copyfile(options["srt_path"], srt_path)
        report("export", 0)
        result = runtime.run_export(
            video_path=options["video_path"], output_path=str(directory / "output.mp4"),
            mode=options["mode"], srt_path=srt_path, audio_path=options["audio_path"],
            output_ratio=options["output_ratio"], audio_quality=options["audio_quality"],
            project_temp_dir=str(directory), on_progress=lambda percent, message: report("export", percent),
        )
        record = asset("exported_video", result)
        return {"output_path": record["path"], "assets": [record]}

    raise ValueError("Unknown CapCap MCP operation.")


def main():
    from contextlib import redirect_stdout
    from dotenv import load_dotenv

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    load_dotenv(root / ".env")
    _apply_job_environment(request["options"])
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # Always run local engines, even when the desktop uses a remote profile.
    os.environ["CAPCAP_RUNTIME_PROFILE"] = "local"
    events = sys.stdout

    def send(**event):
        print(EVENT_PREFIX + json.dumps(event, ensure_ascii=True), file=events, flush=True)

    def report(phase, percent=None):
        send(phase=phase, progress=percent)

    try:
        with redirect_stdout(sys.stderr):
            # Qt font metrics are required for some export styles.
            from PySide6.QtWidgets import QApplication

            app = QApplication([])
            try:
                result = run_job(request["operation"], request["options"], request["directory"], report)
            finally:
                for name, unload in (
                    ("engines.omnivoice_tts", "unload_omnivoice"),
                    ("app.engines.omnivoice_tts", "unload_omnivoice"),
                    ("whisper_processor", "unload_whisper_models"),
                    ("capcut.transport", "close_capcut_pc_bridges"),
                    ("app.capcut.transport", "close_capcut_pc_bridges"),
                ):
                    module = sys.modules.get(name)
                    if module is not None:
                        getattr(module, unload)()
                app.quit()
    except Exception as exc:
        traceback.print_exc()
        send(error=type(exc).__name__)
        code = 1
    else:
        send(result=result)
        code = 0
    # The Windows CUDA/Qt teardown can abort the interpreter after a job has
    # completed. Outputs, progress, and model cleanup are already finished.
    sys.stderr.flush()
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32")
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        kernel.TerminateProcess(kernel.GetCurrentProcess(), code)
    os._exit(code)


if __name__ == "__main__":
    main()
