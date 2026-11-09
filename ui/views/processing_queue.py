import os
from pathlib import Path
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QPushButton,QLabel,QTableWidget,QTableWidgetItem,QFileDialog,QMessageBox,QAbstractItemView,QCheckBox
from PySide6.QtGui import QColor
from services.processing_queue import ProcessingQueue,editor_busy,acquire,release,project_ready_to_export
from services.project_service import ProjectService
from worker_adapters.queue_workers import QueueRunner
from i18n import t


class QueuePanel(QWidget):
    def __init__(self,workspace,launcher):
        super().__init__(launcher)
        self.setStyleSheet("""
            QLabel { color:#9fb3ca; background:transparent; }
            QTableWidget { background:#101b2c; alternate-background-color:#15243a; color:#d7e3f4; gridline-color:#263d58; border:1px solid #263d58; border-radius:8px; selection-background-color:#284969; }
            QHeaderView::section, QTableCornerButton::section { background:#1b2e46; color:#cfe6ff; padding:7px; border:1px solid #263d58; }
            QPushButton { background:#1c304a; color:#d7e3f4; border:1px solid #34506f; border-radius:7px; padding:8px 12px; }
            QPushButton:hover { background:#294564; }
            QPushButton:disabled { color:#68809a; background:#142236; }
        """)
        self.workspace,self.launcher = workspace,launcher
        self.store = ProcessingQueue(workspace)
        self.service = ProjectService(workspace)
        self.runner = None
        self.editor = None
        self.owner = "queue:"+str(id(self))
        layout = QVBoxLayout(self)
        hint = QLabel(t("Performance: configure each video, then run the queue sequentially. Completed projects can be opened in Quality for editing."))
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0,4)
        self.table.setHorizontalHeaderLabels([t("Video"),t("Status"),t("Configuration"),t("Progress / output")])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0,260)
        self.table.setColumnWidth(2,210)
        self.table.doubleClicked.connect(lambda *_:self.open_selected())
        layout.addWidget(self.table,1)
        row = QHBoxLayout()
        self.buttons = {}
        actions = (("Add videos",self.add_videos),("Add folder",self.add_folder),("Configure",self.open_selected),("Open in Quality",lambda:self.open_selected("quality")),("Remove",self.remove),("Move up",lambda:self.move(-1)),("Move down",lambda:self.move(1)),("Retry",self.retry))
        for index,(label,action) in enumerate(actions):
            if index == 4:
                layout.addLayout(row)
                row=QHBoxLayout()
            button = QPushButton(t("Process again" if label=="Retry" else label))
            button.clicked.connect(lambda checked=False,fn=action:fn())
            self.buttons[label]=button
            if label=="Open in Quality":
                button.setToolTip(t("Move this project to Quality and remove it from the Performance queue."))
            row.addWidget(button)
        layout.addLayout(row)
        self.auto_export_checkbox=QCheckBox(t("Automatically export video after processing"))
        self.auto_export_checkbox.setChecked(self.store.auto_export)
        self.auto_export_checkbox.setStyleSheet("color:#cfe6ff; padding:4px;")
        def save_auto_export(checked):
            self.store.auto_export=checked
            self.store.save()
        self.auto_export_checkbox.toggled.connect(save_auto_export)
        layout.addWidget(self.auto_export_checkbox)
        run = QHBoxLayout()
        for label,action in (("Start queue",self.start),("Export selected videos",self.export_selected),("Stop after current video",self.stop_after),("Stop now",self.stop_now)):
            button = QPushButton(t("Process queue" if label=="Start queue" else label))
            button.clicked.connect(action)
            self.buttons[label]=button
            run.addWidget(button)
        self.status = QLabel("")
        self.status.setText(self.store.warning)
        self.status.setWordWrap(True)
        layout.addLayout(run)
        layout.addWidget(self.status)
        self.table.itemSelectionChanged.connect(self._update_export_button)
        self.refresh()

    def selected(self):
        index = self.table.currentRow()
        return self.store.jobs[index] if 0 <= index < len(self.store.jobs) else None

    def selected_jobs(self):
        return [self.store.jobs[index.row()] for index in self.table.selectionModel().selectedRows()
                if index.row()<len(self.store.jobs)]

    def _update_export_button(self):
        if "Export selected videos" not in self.buttons:
            return
        jobs=self.selected_jobs()
        active=self.runner is not None
        ready=bool(jobs) and all(job["status"] in {"processed","completed","failed","interrupted"}
            and project_ready_to_export(self.project(job)) for job in jobs)
        self.buttons["Export selected videos"].setEnabled(not active and ready)

    def project(self,job):
        path = Path(self.service.projects_root)/self.service._build_project_id(job["video"])/"project.json"
        try:
            return self.service.load_project(str(path)) if path.is_file() else None
        except (OSError,ValueError):
            return None

    def refresh(self):
        selected = self.table.currentRow()
        self.table.setRowCount(len(self.store.jobs))
        for row,job in enumerate(self.store.jobs):
            state = self.project(job)
            configured = bool(state and state.settings.get("processing_profile"))
            profile=state.settings.get("processing_profile",{}) if state else {}
            prepare=profile.get("prepare",{})
            summary=(str(prepare.get("mode",""))+" · "+str(prepare.get("source_language","auto"))+" → "+str(prepare.get("target_language","vi"))) if configured else t("Configure")
            for column,value in enumerate((Path(job["video"]).name,t(job["status"].title()),summary,job.get("output") or job.get("message", ""))):
                item = QTableWidgetItem(str(value));item.setToolTip(str(value) if column else job["video"])
                if column==1:item.setForeground(QColor({"processed":"#6ee7d6","completed":"#3ddc97","failed":"#ff7b7b","interrupted":"#f0b347","running":"#7ed8ff"}.get(job["status"],"#a8bad2")))
                self.table.setItem(row,column,item)
        if selected>=0:
            self.table.selectRow(min(selected,len(self.store.jobs)-1))
        active = self.runner is not None and self.runner.isRunning()
        self.buttons["Start queue"].setEnabled(not active)
        for name in ("Configure","Open in Quality"):
            self.buttons[name].setEnabled(not active)
        for name in ("Stop after current video","Stop now"):
            self.buttons[name].setEnabled(active)
        self.auto_export_checkbox.setEnabled(not active)
        self._update_export_button()

    def add_videos(self):
        paths,_=QFileDialog.getOpenFileNames(self,t("Add videos"),"",t("Video Files (*.mp4 *.mkv *.avi *.mov *.webm)"))
        self.add_paths(paths)

    def add_paths(self,paths):
        rejected=self.store.add(paths)
        self.refresh()
        if rejected:
            QMessageBox.information(self,t("Performance"),t("These videos already belong to Quality and cannot be added to Performance:")+"\n\n"+"\n".join(Path(path).name for path in rejected))

    def add_folder(self):
        folder=QFileDialog.getExistingDirectory(self,t("Add folder"))
        if folder:
            self.add_paths([str(p) for p in Path(folder).iterdir() if p.suffix.lower() in {".mp4",".mkv",".avi",".mov",".webm"}])

    def remove(self):
        job=self.selected()
        if job and job["status"]!="running":
            self.store.jobs.remove(job);self.store.save();self.refresh()

    def move(self,direction):
        job=self.selected()
        if not job or job["status"]=="running":return
        index=self.store.jobs.index(job);target=index+direction
        if 0<=target<len(self.store.jobs) and self.store.jobs[target]["status"]!="running":
            self.store.jobs[index],self.store.jobs[target]=self.store.jobs[target],self.store.jobs[index]
            self.store.save();self.refresh();self.table.selectRow(target)

    def retry(self):
        job=self.selected()
        if job:
            self.store.retry(job);self.refresh()

    def open_selected(self,mode="performance"):
        if editor_busy():
            QMessageBox.information(self,t("Processing"),t("A video is being processed. Wait or stop it before opening an editor."));return
        job=self.selected()
        if not job:return
        from services.project_work_mode import project_work_mode
        if project_work_mode(self.workspace,job["video"],jobs=self.store.jobs)=="quality":
            QMessageBox.information(self,t("Performance"),t("This video already belongs to Quality. Remove it from the Performance queue."));return
        try:
            from editor_factory import create_editor
            self.editor=create_editor(mode,on_return=self._returned,on_close=self._close_application)
            self.launcher.hide()
            self.editor.load_video_project(job["video"])
            if mode=="quality":
                # This explicit action transfers ownership; don't leave a
                # second editable/retryable entry in the Performance queue.
                self.store.jobs.remove(job);self.store.save()
        except Exception as exc:
            if self.editor:
                self.editor._terminate_workers();self.editor.hide();self.editor.deleteLater();self.editor=None
            self.launcher.show()
            QMessageBox.critical(self,t("Error"),str(exc))

    def _returned(self):
        if self.editor:
            self.editor.deleteLater();self.editor=None
        self.launcher.show();self.launcher.raise_();self.refresh()

    def _close_application(self):
        if self.editor:
            self.editor.deleteLater();self.editor=None
        # Finish the launcher's event loop as well as the editor. Merely hiding
        # both windows would leave the Python process waiting invisibly.
        self.launcher.reject()

    def start(self):
        self._start_jobs()

    def export_selected(self):
        if editor_busy():
            QMessageBox.information(self,t("Processing"),t("A video is being processed. Wait or stop it before opening an editor."));return
        jobs=self.selected_jobs()
        if not jobs:return
        for job in jobs:
            state=self.project(job)
            if not project_ready_to_export(state):
                QMessageBox.information(self,t("Export"),t("Process the selected videos before exporting."));return
        for job in jobs:
            job.update(status="pending",task="export",message="",output="")
        self.store.save()
        self._start_jobs({job["id"] for job in jobs})

    def _start_jobs(self,job_ids=None):
        if editor_busy():
            QMessageBox.information(self,t("Processing"),t("A video is being processed. Wait or stop it before opening an editor."));return
        pending=[j for j in self.store.jobs if j["status"]=="pending" and (job_ids is None or j["id"] in job_ids)]
        if not pending:return
        from services.project_work_mode import project_work_mode
        for job in pending:
            if project_work_mode(self.workspace,job["video"],jobs=self.store.jobs)=="quality":
                QMessageBox.information(self,t("Performance"),t("This video already belongs to Quality. Remove it from the Performance queue."));return
            state=self.project(job)
            if not state or not state.settings.get("processing_profile"):
                QMessageBox.information(self,t("Configuration"),t("Configure each pending video before starting the queue."));return
        if not acquire(self.owner):return
        self._launcher_enabled={}
        for name in ("split_btn","clean_video_btn","import_project_btn","resource_btn","cpu_btn","gpu_btn","ui_mode_combo"):
            button=getattr(self.launcher,name,None)
            if button is not None:
                self._launcher_enabled[button]=button.isEnabled();button.setEnabled(False)
        self.runner=QueueRunner(self.workspace,self.store,self,auto_export=self.auto_export_checkbox.isChecked(),job_ids=job_ids)
        self.runner.update.connect(self._update)
        self.runner.output.connect(self._output)
        self.runner.finished.connect(self._finished)
        try:self.runner.start()
        except Exception:
            release(self.owner)
            self.runner.deleteLater();self.runner=None
            for button,enabled in self._launcher_enabled.items():
                button.setEnabled(enabled)
            self.refresh()
            raise
        self.refresh()

    def _update(self,job_id,status,message):
        for row,job in enumerate(self.store.jobs):
            if job["id"]==job_id:
                changed=job["status"]!=status
                job.update(status=status,message=message)
                if status=="processed":job["output"]=""
                if changed and status in {"failed","interrupted"}:
                    state=self.project(job)
                    if state:
                        for step,value in list(state.steps.items()):
                            if value=="running":state.set_step_status(step,"failed")
                        self.service.save_project(state)
                self.table.item(row,1).setText(t(status.title()))
                self.table.item(row,1).setForeground(QColor({"processed":"#6ee7d6","completed":"#3ddc97","failed":"#ff7b7b","interrupted":"#f0b347","running":"#7ed8ff"}.get(status,"#a8bad2")))
                self.table.item(row,3).setText(t(message.splitlines()[-1]) if message else "")
                self.table.item(row,3).setToolTip(message)
                if changed:self.store.save()
                self.status.setText(Path(job["video"]).name+": "+t(message[-500:]))
                return

    def _output(self,job_id,path):
        for job in self.store.jobs:
            if job["id"]==job_id:job["output"]=path

    def _finished(self):
        self.runner.wait()
        self.runner.deleteLater();self.runner=None
        release(self.owner)
        for button,enabled in getattr(self,"_launcher_enabled",{}).items():button.setEnabled(enabled)
        self.store.save();self.refresh()

    def stop_after(self):
        if self.runner:
            self.runner.stop_after=True
            self.status.setText(t("The queue will stop after the current video."))

    def stop_now(self):
        if self.runner:self.runner.cancel()
