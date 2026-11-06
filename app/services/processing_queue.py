"""Persistent sequential jobs. Project files remain the source of configuration."""
from __future__ import annotations
import json
import os
import threading
from pathlib import Path
from uuid import uuid4

_lock = threading.Lock()
_owner = None
ENV_KEYS = ("CAPCAP_DEVICE", "CAPCAP_OMNIVOICE_STEPS", "ELEVENLABS_MODEL", "OPENAI_PROVIDER", "OPENAI_MODEL", "OLLAMA_MODEL", "TRANSCRIPTION_ENGINE", "OCR_SUBTITLE_RECT")


def project_ready_to_export(state):
    if state is None:
        return False
    if "queue_processing_ready" in state.settings:
        return state.settings["queue_processing_ready"] is True
    # Existing completed projects predate the explicit processing/render split.
    profile=state.settings.get("processing_profile",{})
    prepare=profile.get("prepare",{})
    srt=state.artifacts.get("subtitle_translated_srt") or state.artifacts.get("srt_translated","")
    if prepare.get("skip_translation"):
        srt=srt or state.artifacts.get("subtitle_original_srt") or state.artifacts.get("srt_original","")
    if not os.path.isfile(srt):return False
    if prepare.get("mode",state.mode) in {"voice","both"}:
        audio=profile.get("existing_audio_path","") if profile.get("use_existing_audio") else state.artifacts.get("voice_vi","")
        return os.path.isfile(audio)
    return True


def busy():
    with _lock:
        return _owner is not None


def acquire(owner):
    global _owner
    with _lock:
        if _owner is not None:
            return False
        _owner = owner
        return True


def release(owner):
    global _owner
    with _lock:
        if _owner == owner:
            _owner = None


def editor_busy():
    """Include manual processing in an already open editor."""
    if busy():
        return True
    from PySide6.QtWidgets import QApplication, QMainWindow
    for window in QApplication.topLevelWidgets() if QApplication.instance() else ():
        if not isinstance(window,QMainWindow):
            continue
        if getattr(window, "_pipeline_active", False):
            return True
        for name in ("voice_thread", "prepare_workflow_thread", "export_thread", "transcription_thread", "translation_thread", "vocal_thread", "extraction_thread", "_voice_sample_preview_thread"):
            worker = getattr(window, name, None)
            try:
                if worker is not None and worker.isRunning():
                    return True
            except RuntimeError:
                pass
        for worker in (getattr(window, "_segment_preview_threads", {}) or {}).values():
            try:
                if worker.isRunning():
                    return True
            except RuntimeError:
                pass
        for worker in getattr(window,"_retiring_workers",[]) or []:
            try:
                if worker.isRunning():
                    return True
            except RuntimeError:
                pass
    return False


class ProcessingQueue:
    def __init__(self, workspace):
        self.path = Path(workspace)/"processing_queue.json"
        self.jobs = []
        self.warning = ""
        self.auto_export = False
        if self.path.is_file():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.jobs = saved.get("jobs", [])
                self.auto_export = saved.get("auto_export",False) is True
                if not isinstance(self.jobs,list) or any(not isinstance(j,dict) or not j.get("id") or not j.get("video") for j in self.jobs):
                    raise ValueError("Invalid queue data")
            except (ValueError,TypeError):
                backup=self.path.with_name("processing_queue.invalid-"+uuid4().hex+".json")
                self.path.rename(backup)
                self.jobs=[]
                self.warning="The previous queue could not be read. A backup was saved at: "+str(backup)
        for job in self.jobs:
            if job.get("status") == "running":
                job.update(status="interrupted", message="Interrupted; retry to continue from saved project data.")

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"version": 1, "jobs": self.jobs,"auto_export":self.auto_export}, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def add(self, paths):
        from services.project_work_mode import project_work_mode, video_key
        rejected=[]
        existing = {video_key(j["video"]) for j in self.jobs}
        for value in paths:
            path = os.path.abspath(value)
            key = video_key(path)
            if os.path.isfile(path) and project_work_mode(self.path.parent,path,jobs=self.jobs)=="quality":
                rejected.append(path)
                continue
            if key not in existing and os.path.isfile(path):
                self.jobs.append(dict(id=uuid4().hex, video=path, status="pending", message="", output=""))
                existing.add(key)
        self.save()
        return rejected

    def retry(self, job):
        if job.get("status") != "running":
            job.update(status="pending",task="process", message="", output="")
            self.save()
