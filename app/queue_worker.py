"""Isolated queue entrypoint: shared workflows, no Main UI or video player."""
import json
import os
import sys
import traceback
from pathlib import Path


def run_job(workspace, video, config, progress=print, *, task="full"):
    from services.workflow_runtime import WorkflowRuntime
    from services.gui_project_bridge import GUIProjectBridge
    from audio_mixer import mix_audio_tracks
    from services.voice_edit_service import group_voice_segments, merge_generated_voice_segments
    runtime = WorkflowRuntime(workspace)
    if task not in {"process","export","full"}:
        raise ValueError("Unknown queue task")
    prepare = dict(config["prepare"])
    service = runtime.project_service
    state_path = service.project_file(str(Path(service.projects_root)/service._build_project_id(video)))
    if task!="export" and Path(state_path).is_file():
        state=service.load_project(state_path)
        state.set_setting("queue_processing_ready",False)
        service.save_project(state)
    if task!="export":
        state_path = runtime.run_prepare(video, **prepare,
            step_callback=lambda *args: progress(" | ".join(str(a) for a in args)))
    if hasattr(state_path,"project_root"):
        state_path = service.project_file(state_path.project_root)
    state = service.load_project(state_path)
    from services.processing_queue import project_ready_to_export
    if task=="export" and not project_ready_to_export(state):
        raise RuntimeError("Process this video before exporting it.")
    context = GUIProjectBridge(service).load_context(state)
    segments = context["current_translated_segments"] or context["current_segments"]
    assignments = state.settings.get("speaker_voice_assignments", {}) or {}
    for seg in segments:
        assignment = assignments.get(seg.get("speaker", ""), {}) or {}
        if assignment.get("voice"):
            seg["voice_name"] = assignment["voice"]
    mode = prepare["mode"]
    generated = context["last_voice_vi_path"] if task=="export" else ""
    cache = str(Path(state.project_root)/"audio"/"tts_segments")
    existing_audio = str(config.get("existing_audio_path", "")) if config.get("use_existing_audio") else ""
    if config.get("use_existing_audio") and not os.path.isfile(existing_audio):
        raise RuntimeError("The configured existing audio file is missing.")
    if mode in {"voice", "both"} and not existing_audio and task!="export":
        voice_segments=group_voice_segments(segments)
        for seg in voice_segments:
            assignment=assignments.get(seg.get("speaker",""),{}) or {}
            if assignment.get("voice"):seg["voice_name"]=assignment["voice"]
        result = runtime.run_voice(segments=voice_segments, output_dir=str(Path(state.project_root)/"audio"),
            project_state_path=state_path, project_temp_dir=cache,
            on_progress=progress, **config["voice"])
        generated = result["voice_track"]
        state = service.load_project(state_path)
        merge_generated_voice_segments(segments,result.get("segments",[]))
        bridge=GUIProjectBridge(service)
        from subtitle_builder import generate_srt
        subtitle_path=context["last_translated_srt_path"] or str(Path(state.project_root)/"subtitle"/"subtitle.srt")
        generate_srt(segments,subtitle_path)
        bridge.persist_translation(state,context["current_segment_models"],segments,subtitle_path)
        from tts_processor import normalizer_dictionary_fingerprint
        signature_segments=group_voice_segments(segments)
        for seg in signature_segments:
            assignment=assignments.get(seg.get("speaker",""),{}) or {}
            if assignment.get("voice"):seg["voice_name"]=assignment["voice"]
        state.set_setting("voice_signature",service.build_voice_signature(signature_segments,
            voice_name=config["voice"]["voice_name"],voice_speed=config["voice"].get("voice_speed",1),
            audio_handling_mode=config["voice"].get("audio_handling_mode","fast"),
            timing_sync_mode=config["voice"].get("timing_sync_mode","off"),
            model_id=config["voice"].get("elevenlabs_model_id"),language_code=config["voice"].get("target_language"),
            normalizer_signature=normalizer_dictionary_fingerprint(state.settings.get("normalizer_dictionary",{}))))
        service.save_project(state)
        context=bridge.load_context(state)
    if mode in {"voice","both"} and not existing_audio and not Path(generated).is_file():
        raise RuntimeError("The generated voice audio is missing. Process this video again before exporting.")
    style = dict(config.get("export", {}).get("subtitle_style", {}))
    style["word_timings"] = [list(seg.get("words",[])) for seg in segments]
    style["manual_highlights"] = [list(seg.get("manual_highlights",[])) + list(seg.get("auto_highlights",[])) for seg in segments]
    warps = list(state.settings.get("video_time_warps", []))
    style["video_time_warps"] = warps
    duration = runtime.voice_workflow.engine_runtime.get_video_duration(video)
    from services.time_warp_plan import compile_plan
    duration = max(float(duration)+sum(w.duration for w in compile_plan(warps)), max((float(s.get("end", 0)) for s in segments), default=0))
    from app.layers.timeline import Timeline
    from app.layers.sync_bridge import ensure_v1_a1_tracks, sync_segments_to_dub_subtitle_layers, sync_tts_to_dub_subtitle_layers
    timeline_file=str(state.artifacts.get("timeline") or Path(state.project_root)/"timeline"/"timeline.json")
    timeline=Timeline.from_dict(json.loads(Path(timeline_file).read_text(encoding="utf-8"))) if Path(timeline_file).is_file() else Timeline(duration=duration)
    ensure_v1_a1_tracks(timeline,video,runtime.voice_workflow.engine_runtime.get_video_duration(video))
    sync_segments_to_dub_subtitle_layers(timeline,segments)
    if generated:sync_tts_to_dub_subtitle_layers(timeline,generated,segments)
    timeline.duration=max(timeline.duration,duration)
    service.save_json_artifact(state,"timeline","timeline/timeline.json",timeline.to_dict())
    if task!="export":
        state.set_setting("queue_processing_ready",True)
        state.set_step_status("export","pending")
        service.save_project(state)
    if task=="process":
        return {"status":"processed","output":"","project":state_path}
    tracks = []
    audio = config.get("audio", {})
    original = context["last_music_path"] if prepare.get("audio_handling_mode") == "clean" else context["last_extracted_audio"]
    if original:
        tracks.append(dict(path=original, start=0, end=duration, time_warps=warps, **audio.get("original", {})))
    if generated:
        tracks.append(dict(path=generated, start=0, end=duration, **audio.get("tts", {})))
    tracks.extend(audio.get("music", []))
    mixed = ""
    if existing_audio:
        mixed = existing_audio
    elif mode in {"voice", "both"}:
        mixed = str(Path(state.project_root)/"audio"/"queue_mix.wav")
        mix_audio_tracks(tracks=tracks, output_wav_path=mixed, total_duration_ms=int(duration*1000))
        state.set_artifact("mixed_vi",mixed)
        state.set_step_status("mix_audio","done")
        service.save_project(state)
    export = dict(config["export"])
    export["subtitle_style"] = style
    folder = Path(config.get("output_dir") or Path(state.project_root)/"export")
    folder.mkdir(parents=True, exist_ok=True)
    # Each project owns a unique filename, including equal basenames in different folders.
    output = folder/(state.project_id+"_final.mp4")
    if output.exists():
        from uuid import uuid4
        output = folder/(state.project_id+"_"+uuid4().hex[:8]+"_final.mp4")
    result = runtime.run_export(video_path=video, output_path=str(output), mode=mode,
        srt_path=context["last_translated_srt_path"] or context["last_original_srt_path"], audio_path=mixed,
        project_state_path=state_path, project_temp_dir=str(Path(state.project_root)/"export"/"cache"),
        on_progress=lambda *args: progress(" | ".join(str(a) for a in args)), **export)
    return {"status":"completed","output": result, "project": state_path}


def main():
    spec = json.loads(Path(os.environ["CAPCAP_QUEUE_JOB_FILE"]).read_text(encoding="utf-8"))
    from services.processing_queue import ENV_KEYS
    os.environ.update({str(k): str(v) for k,v in spec["config"].get("environment", {}).items() if k in ENV_KEYS})
    # Qt fonts are needed for text overlays during export; no GUI/player is loaded.
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    try:
        result = run_job(spec["workspace"], spec["video"], spec["config"], lambda text: print(text, flush=True),task=spec.get("task","full"))
        Path(spec["result_file"]).write_text(json.dumps(result), encoding="utf-8")
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    main()
