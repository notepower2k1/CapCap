"""Persistent launcher ownership, independent of whether an editor is open."""
import json
import os
from pathlib import Path


def video_key(path):
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def project_work_mode(workspace, video, *, jobs=None):
    from services.project_service import ProjectService
    service=ProjectService(str(workspace))
    project=Path(service.projects_root)/service._build_project_id(video)/"project.json"
    if project.is_file():
        state=service.load_project(str(project))
        # Projects from before the queue feature belong to the full editor.
        mode=state.settings.get("ui_work_mode","quality")
        return mode if mode in {"quality","performance"} else "quality"
    if jobs is None:
        queue_file=Path(workspace)/"processing_queue.json"
        jobs=json.loads(queue_file.read_text(encoding="utf-8")).get("jobs",[]) if queue_file.is_file() else []
    if any(video_key(j["video"])==video_key(video) for j in jobs):
        return "performance"
    # A failed cleanup may leave a locked project directory after deleting
    # its manifest. Keep it assigned until cleanup succeeds or it is reopened.
    return "quality" if project.parent.is_dir() else None
