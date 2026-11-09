import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from PySide6.QtCore import QThread, Signal
from runtime_paths import subprocess_hidden_kwargs


class QueueRunner(QThread):
    update = Signal(str, str, str)
    output = Signal(str, str)

    def __init__(self, workspace, store, parent=None, *, auto_export=True, job_ids=None):
        super().__init__(parent)
        self.workspace = str(workspace)
        self.store = store
        self.stop_after = False
        self.cancelled = threading.Event()
        self.process = None
        self.auto_export = bool(auto_export)
        self.job_ids = set(job_ids) if job_ids is not None else None

    def cancel(self):
        self.cancelled.set()
        process = self.process
        if process is not None and process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, **subprocess_hidden_kwargs())
            else:
                process.terminate()

    def run(self):
        from services.project_service import ProjectService
        service = ProjectService(self.workspace)
        visited = set()
        while not self.cancelled.is_set():
            job = next((dict(j) for j in self.store.jobs if j.get("status") == "pending" and j["id"] not in visited
                        and (self.job_ids is None or j["id"] in self.job_ids)), None)
            if job is None:
                return
            visited.add(job["id"])
            task="export" if job.get("task")=="export" else ("full" if self.auto_export else "process")
            self.update.emit(job["id"], "running", "Preparing")
            try:
                state_path = Path(service.projects_root)/service._build_project_id(job["video"])/"project.json"
                state = service.load_project(str(state_path))
                config = state.settings.get("processing_profile")
                if not config:
                    raise RuntimeError("Configure this video before starting the queue.")
                directory = Path(state.project_root)/"logs"/"queue"
                directory.mkdir(parents=True, exist_ok=True)
                spec, result = directory/(job["id"]+".json"), directory/(job["id"]+"-result.json")
                result.unlink(missing_ok=True)
                spec.write_text(json.dumps(dict(workspace=self.workspace,video=job["video"],config=config,result_file=str(result),task=task),ensure_ascii=False),encoding="utf-8")
                env = dict(os.environ, CAPCAP_QUEUE_JOB_FILE=str(spec), CAPCAP_RUN_QUEUE_WORKER="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
                env.pop("CAPCAP_RUN_REMOTE_API_SERVER",None)
                root = str(Path(__file__).resolve().parents[2])
                command = [sys.executable,"--queue-worker"] if getattr(sys,"frozen",False) else [sys.executable,str(Path(root)/"app"/"queue_worker.py")]
                last = 0
                tail = ""
                with (directory/(job["id"]+".log")).open("w",encoding="utf-8") as log:
                    self.process = subprocess.Popen(command,env=env,cwd=self.workspace,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding="utf-8",errors="replace",**subprocess_hidden_kwargs())
                    if self.cancelled.is_set():
                        self.cancel()
                    for line in self.process.stdout:
                        log.write(line)
                        tail = (tail+line)[-3000:]
                        now = time.monotonic()
                        if now-last >= .25:
                            self.update.emit(job["id"],"running",line.strip()[:500])
                            last = now
                    code = self.process.wait()
                    self.process.stdout.close()
                if self.cancelled.is_set():
                    self.update.emit(job["id"],"interrupted","Stopped. Saved project data is retained.")
                    return
                if code != 0 or not result.is_file():
                    raise RuntimeError(tail or f"Worker exited with code {code}")
                data = json.loads(result.read_text(encoding="utf-8"))
                if task=="process":
                    saved=service.load_project(str(state_path))
                    if data.get("status")!="processed" or not saved.settings.get("queue_processing_ready"):
                        raise RuntimeError("The worker did not finish processing this project.")
                    self.update.emit(job["id"],"processed","Processing finished. Ready to export.")
                else:
                    if not Path(data.get("output","")).is_file():
                        raise RuntimeError("The worker finished without an exported video.")
                    self.output.emit(job["id"],str(data["output"]))
                    self.update.emit(job["id"],"completed","Completed")
            except Exception as exc:
                self.update.emit(job["id"],"failed",str(exc))
            finally:
                if self.process is not None and self.process.poll() is None:
                    self.cancel()
                    self.process.wait()
                self.process = None
            if self.stop_after:
                return
