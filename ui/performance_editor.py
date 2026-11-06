"""The existing editor/workflows with a still preview and virtual subtitle table."""
import os
from pathlib import Path
import subprocess
from uuid import uuid4
from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QThread, Signal
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QDoubleSpinBox, QTableView, QSplitter, QMessageBox, QComboBox
from main_window import VideoTranslatorGUI
from runtime_paths import bin_path, subprocess_hidden_kwargs
from i18n import t


class SubtitleTable(QAbstractTableModel):
    edited = Signal()
    def __init__(self, parent=None):
        super().__init__(parent)
        self.segments = []

    def set_segments(self, segments):
        self.beginResetModel()
        self.segments = segments
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.segments)

    def columnCount(self, parent=QModelIndex()):
        return 3

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return [t("Start"),t("End"),t("Subtitle")][section]
        return super().headerData(section,orientation,role)

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole,Qt.EditRole):
            item = self.segments[index.row()]
            return item.get("text","") if index.column()==2 else f"{float(item.get('start' if index.column()==0 else 'end',0)):.3f}"

    def flags(self,index):
        return super().flags(index) | (Qt.ItemIsEditable if index.column()==2 else Qt.NoItemFlags)

    def setData(self,index,value,role=Qt.EditRole):
        if not index.isValid() or index.column()!=2 or role!=Qt.EditRole:
            return False
        self.segments[index.row()]["text"] = str(value)
        self.dataChanged.emit(index,index,[role,Qt.DisplayRole])
        self.edited.emit()
        return True


class StillFrameWorker(QThread):
    ready = Signal(str,str)
    def __init__(self,source,output,seconds,parent=None):
        super().__init__(parent)
        self.source,self.output,self.seconds = source,output,seconds
        self.process = None
    def run(self):
        try:
            self.process = subprocess.Popen([bin_path("ffmpeg","ffmpeg.exe"),"-v","error","-y","-ss",str(self.seconds),"-i",self.source,"-frames:v","1","-vf","scale=960:960:force_original_aspect_ratio=decrease",self.output],stdout=subprocess.PIPE,stderr=subprocess.PIPE,**subprocess_hidden_kwargs())
            _,error = self.process.communicate(timeout=30)
            if self.process.returncode:
                raise RuntimeError(error.decode("utf-8",errors="replace"))
            self.ready.emit(self.output,"")
        except Exception as exc:
            if self.process and self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.ready.emit("",str(exc))


class PerformanceEditor(VideoTranslatorGUI):
    performance_mode = True
    returned = Signal()
    exit_requested = Signal()

    def __init__(self):
        self._frame_worker = None
        self._returning = False
        self._closing = False
        self._still_time = 0.0
        super().__init__()
        self.media_player.position = lambda: int(self._still_time * 1000)
        self._bind_preview_edit_signals()
        self.setWindowTitle("CapCap — Performance")
        self.header_brand_label.setText("CapCap · Performance")
        self.preview_timeline_splitter.hide()
        top = QWidget()
        row = QHBoxLayout(top)
        row.setContentsMargins(0,0,0,0)
        row.addWidget(self.preview_card,2)
        self.subtitle_inspector_shell.setMinimumWidth(300)
        self.subtitle_inspector_shell.setMaximumWidth(16777215)
        self.inspector_stack.setMinimumWidth(280)
        self.inspector_stack.setMaximumWidth(16777215)
        row.addWidget(self.subtitle_inspector_shell,2)
        self.subtitle_inspector_shell.show()
        self.inspector_stack.setCurrentIndex(0)
        self.segment_editor_container.hide()
        # The hidden segment editor used to absorb this card's spare height.
        # Keep the action rows together and put remaining space below them.
        self.subtitle_inspector_card.layout().addStretch(1)
        self._performance_table = QTableView()
        self._subtitle_table_model = SubtitleTable(self)
        self._performance_table.setModel(self._subtitle_table_model)
        self._performance_table.horizontalHeader().setStretchLastSection(True)
        self._subtitle_table_model.edited.connect(self._table_edited)
        self._performance_table.clicked.connect(lambda index:self._select_static_subtitle(index.row()))
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(top)
        splitter.addWidget(self._performance_table)
        splitter.setStretchFactor(0,3)
        splitter.setStretchFactor(1,2)
        self.right_layout.addWidget(splitter,1)
        controls = QHBoxLayout()
        self._frame_time = QDoubleSpinBox()
        self._frame_time.setRange(0,864000)
        self._frame_time.setSuffix(" s")
        self._frame_button = QPushButton(t("Change frame"))
        self._frame_button.clicked.connect(self._request_still)
        hint=QLabel(t("Static preview — animations are not played"))
        hint.setWordWrap(True)
        controls.addWidget(hint,1)
        controls.addWidget(self._frame_time)
        controls.addWidget(self._frame_button)
        self.right_layout.insertLayout(0,controls)
        layers = QHBoxLayout()
        self._layer_choices = QComboBox()
        self._layer_choices.currentIndexChanged.connect(self._select_layer_choice)
        layers.addWidget(self.add_layer_btn)
        layers.addWidget(self._layer_choices,1)
        self._delete_layer_btn = QPushButton(t("Delete"))
        self._delete_layer_btn.clicked.connect(self._delete_layer_choice)
        layers.addWidget(self._delete_layer_btn)
        self.right_layout.insertLayout(1,layers)
        self._layer_menu.addAction(t("Add Music Layer"),self.add_music_layer)
        actions=QHBoxLayout()
        actions.addWidget(self.batch_fit_voice_btn)
        regenerate=QPushButton(t("Regenerate selected voice"))
        regenerate.clicked.connect(lambda:self.preview_segment_audio(self._performance_table.currentIndex().row()))
        actions.addWidget(regenerate)
        actions.addStretch()
        self.right_layout.addLayout(actions)
        self.right_layout.addWidget(self.progress_bar)
        self._performance_table.setStyleSheet("QTableView { background:#111927; alternate-background-color:#17263a; color:#d7e3f4; gridline-color:#30425b; } QHeaderView::section { background:#1d2c40; color:#a8bad2; padding:6px; border:1px solid #30425b; }")
        self._performance_table.setAlternatingRowColors(True)
        # Keep playback hidden, but reuse the source/layer tools on the still.
        transport = self.preview_card.layout().itemAt(self.preview_card.layout().count()-1)
        def hide_layout(layout):
            if layout is None:return
            for index in range(layout.count()):
                item=layout.itemAt(index)
                if item.widget():item.widget().hide()
                elif item.layout():hide_layout(item.layout())
        if transport and transport.layout():hide_layout(transport.layout())
        self.preview_card.layout().addWidget(self.preview_blur_group_widget)
        self.preview_blur_group_widget.layout().addStretch(1)
        self.preview_blur_group_widget.show()
        for button in (self.blur_add_btn,self.add_logo_btn,self.add_mask_btn,self.add_text_btn):
            button.show()
        self.ocr_translator_btn.hide()
        # The still has no native MPV surface to cover child widgets. Do not
        # copy the old tool-window flags: they can retain TransparentForInput
        # from the overlay's initially disabled state on Windows.
        self.ocr_region_overlay.setParent(self.video_view,Qt.Widget)
        self.ocr_region_overlay.setWindowFlag(Qt.WindowTransparentForInput,False)
        self.ocr_region_overlay.setWindowFlag(Qt.WindowDoesNotAcceptFocus,False)
        for name in ("run_all_btn","export_btn","play_btn","stop_btn","preview_btn","preview_5s_btn","blur_area_btn","inspector_collapse_tab"):
            control = getattr(self,name,None)
            if control:
                control.hide()
        self.workspace_layout_toggle_action.setVisible(False)
        self.more_actions_btn.show()
        self.exit_project_action.setText(t("Back to Launcher"))
        self._refresh_layer_choices()

    def apply_responsive_layout(self,*args):
        self.setMinimumSize(1000,680)

    def sync_left_panel_container_width(self):
        import shiboken6
        if shiboken6.isValid(self):
            super().sync_left_panel_container_width()

    def set_workspace_layout_mode(self,*args):
        pass

    def refresh_ui_state(self):
        super().refresh_ui_state()
        # Shared UI refreshes can make the render-preview button visible again.
        for name in ("preview_btn","run_all_btn","export_btn","preview_5s_btn","ocr_translator_btn"):
            button=getattr(self,name,None)
            if button is not None:
                button.hide()
        action=getattr(self,"exit_project_action",None)
        if action is not None:
            action.setText(t("Back to Launcher"))

    def _select_video_layer_for_inspector(self):
        super()._select_video_layer_for_inspector()
        self._refresh_layer_choices()

    def _show_default_inspector(self):
        super()._show_default_inspector()

    def _refresh_layer_choices(self):
        if not hasattr(self,"_layer_choices"):
            return
        selected = str(getattr(self.timeline,"_selected_layer_id","") or "")
        blocked = self._layer_choices.blockSignals(True)
        self._layer_choices.clear()
        self._layer_choices.addItem(t("Subtitle"),"")
        for track in self.timeline._timeline.tracks:
            for layer in track.layers:
                if str(getattr(layer.type,"value",layer.type)) not in {"subtitle","dub_subtitle"}:
                    self._layer_choices.addItem(f"{track.name} · {layer.name}",layer.id)
        index=self._layer_choices.findData(selected)
        self._layer_choices.setCurrentIndex(max(0,index))
        self._layer_choices.blockSignals(blocked)
        self._delete_layer_btn.setEnabled(bool(selected) and index>=0)

    def _select_layer_choice(self,*args):
        layer_id = str(self._layer_choices.currentData() or "")
        self.timeline.select_layer(layer_id)
        self.on_timeline_layer_selected(layer_id)
        if not layer_id:
            self._switch_inspector("subtitle")
        self._refresh_layer_choices()

    def _delete_layer_choice(self):
        if self._layer_choices.currentData():
            self.delete_selected_timeline_segment()
            self._refresh_layer_choices()
            self.refresh_timed_layer_preview()

    def on_timeline_layer_selected(self,layer_id):
        super().on_timeline_layer_selected(layer_id)
        self._refresh_layer_choices()

    def on_add_timeline_layer(self,layer_type="subtitle"):
        before={layer.id for track in self.timeline._timeline.tracks for layer in track.layers}
        super().on_add_timeline_layer(layer_type)
        added=[layer for track in self.timeline._timeline.tracks for layer in track.layers if layer.id not in before]
        if added and layer_type!="subtitle":
            self.timeline.select_layer(added[-1].id)
            self.on_timeline_layer_selected(added[-1].id)
        self._refresh_layer_choices()
        self.refresh_timed_layer_preview()

    def add_music_layer(self):
        before={layer.id for track in self.timeline._timeline.tracks for layer in track.layers}
        super().add_music_layer()
        added=[layer for track in self.timeline._timeline.tracks for layer in track.layers if layer.id not in before]
        if added:
            self.timeline.select_layer(added[-1].id)
            self.on_timeline_layer_selected(added[-1].id)
        self._refresh_layer_choices()

    def persist_current_timeline_project_data(self):
        super().persist_current_timeline_project_data()
        self._refresh_layer_choices()

    def sync_segment_editor_rows(self):
        if hasattr(self,"_subtitle_table_model"):
            self._subtitle_table_model.set_segments(self.current_translated_segments or self.current_segments)

    def _table_edited(self):
        self._voiceover_force_refresh = True
        if self.current_translated_segments:
            self.translated_text.setPlainText(self.format_to_srt(self.current_translated_segments))
            self._regenerate_translated_srt_from_segments()
        else:
            self.transcript_text.setPlainText(self.format_to_srt(self.current_segments))
            if self.last_original_srt_path:
                from subtitle_builder import generate_srt
                generate_srt(self.current_segments,self.last_original_srt_path)
            self.persist_transcription_project_data(self.current_segments,self.last_original_srt_path)
        self.sync_live_subtitle_preview()

    def _select_static_subtitle(self,index):
        self._selected_segment_index = index
        self.sync_live_subtitle_preview()

    def ensure_media_backend_ready(self):
        return

    def setup_media_player(self):
        return

    def schedule_timeline_visual_refresh(self,**kwargs):
        return

    def sync_preview_audio_track_to_output(self,**kwargs):
        return

    def _schedule_preview_audio_refresh(self,**kwargs):
        return

    def schedule_live_video_filter_preview(self,*args,**kwargs):
        self._refresh_static_effects()

    def refresh_timeline_waveform(self):
        return

    def _preload_active_voice_if_needed(self,*args,**kwargs):
        return

    def refresh_timeline_video_thumbnails(self):
        return

    def _run_pending_timeline_visual_refresh(self):
        return

    def schedule_auto_frame_preview(self,*args,**kwargs):
        return

    def schedule_seek_frame_preview(self,*args,**kwargs):
        return

    def _set_live_subtitle_effects(self,*args,**kwargs):
        return

    def refresh_timed_layer_preview(self,*args,**kwargs):
        if getattr(self,"_project_loading_in_progress",False) or not hasattr(self,"timeline"):
            return
        selected=str(getattr(self.timeline,"_selected_layer_id","") or "")
        self._refresh_text_layer_preview(selected)
        blur_layers=[]
        mask_layers=[]
        logo_track=None
        for track in self.timeline._timeline.tracks:
            for layer in track.layers:
                if not self._layer_is_active_at_preview_time(layer):
                    continue
                kind=str(getattr(layer.type,"value",layer.type))
                if kind=="blur":blur_layers.append(layer)
                elif kind=="mask":mask_layers.append(layer)
                elif kind=="image" and track.name=="L1 Logo":logo_track=track
        self.video_view.blur_overlay.attach_to_view(self.video_view)
        self.video_view.set_blur_regions_normalized([self._blur_payload(layer) for layer in blur_layers])
        blur_index=next((i for i,layer in enumerate(blur_layers) if layer.id==selected),-1)
        self.video_view.set_blur_active_index(blur_index)
        self.video_view.set_blur_edit_enabled(blur_index>=0 and self._blur_effect_enabled())
        mask_index=next((i for i,layer in enumerate(mask_layers) if layer.id==selected),-1)
        self.video_view.set_mask_regions(self._current_mask_regions_payload(),
            active_index=max(0,mask_index),editable=mask_index>=0 and getattr(self,"_mask_track_preview_visible",True))
        if logo_track is not None:
            active=[layer for layer in logo_track.layers if self._layer_is_active_at_preview_time(layer)]
            target=next((layer for layer in active if layer.id==selected),active[0])
            self._show_logo_overlay(logo_track,target,editable=target.id==selected)
        else:
            self.video_view.clear_logo()
        self._refresh_static_effects()

    @staticmethod
    def _blur_payload(layer):
        return dict(x=layer.position_x,y=layer.position_y,width=layer.width,height=layer.height,
            start=layer.start,end=layer.end,blur_strength=layer.blur_strength,
            blur_opacity=layer.blur_opacity,pixelate=layer.pixelate,pixelate_size=layer.pixelate_size)

    def _current_blur_regions_payload(self):
        model=getattr(getattr(self,"timeline",None),"_timeline",None)
        return [self._blur_payload(layer) for track in getattr(model,"tracks",[]) for layer in track.layers
                if str(getattr(layer.type,"value",layer.type))=="blur"]

    def on_preview_blur_region_changed(self):
        if getattr(self,"_is_cleaning_or_resetting",False):
            return
        # Only active regions are editable on this frame. Preserve all other
        # timed clips instead of rebuilding B1 from a partial overlay payload.
        layers=[layer for track in self.timeline._timeline.tracks for layer in track.layers
                if str(getattr(layer.type,"value",layer.type))=="blur" and self._layer_is_active_at_preview_time(layer)]
        for layer,rect in zip(layers,self.video_view.blur_overlay._regions):
            layer.position_x,layer.position_y=rect.x(),rect.y()
            layer.width,layer.height=rect.width(),rect.height()
        self.schedule_timeline_project_persist(blur_state=True)
        self._refresh_static_effects()

    def _sync_blur_layer_to_preview(self,layer):
        self.schedule_timeline_project_persist(blur_state=True)
        self.refresh_timed_layer_preview()

    def persist_project_mask_state(self,*,regions=None):
        super().persist_project_mask_state(regions=self._current_mask_regions_payload(include_inactive=True) if regions is None else regions)

    def _refresh_static_effects(self):
        view=getattr(self,"video_view",None)
        timeline=getattr(getattr(self,"timeline",None),"_timeline",None)
        if not view or not timeline or not hasattr(view,"set_effect_layers"):
            return
        effects=[]
        for track in timeline.tracks:
            for layer in track.layers:
                kind=str(getattr(layer.type,"value",layer.type))
                if kind not in {"blur","mask"} or not self._layer_is_active_at_preview_time(layer):
                    continue
                if kind=="blur" and not self._blur_effect_enabled():
                    continue
                if kind=="mask" and not getattr(self,"_mask_track_preview_visible",True):
                    continue
                effects.append(dict(x=layer.position_x,y=layer.position_y,width=layer.width,height=layer.height,
                    mode=("pixelate" if layer.pixelate else "blur") if kind=="blur" else layer.mode,
                    color=getattr(layer,"color","#000000"),opacity=getattr(layer,"blur_opacity",getattr(layer,"opacity",1.0)),
                    blur_strength=layer.blur_strength,pixelate_size=layer.pixelate_size))
        view.set_effect_layers(effects,mirror=getattr(self,"_video_mirror_horizontal",False))

    def apply_preview_blur_region(self,**kwargs):
        self._refresh_static_effects()

    def _apply_mask_to_preview(self,**kwargs):
        self._refresh_static_effects()

    def sync_live_subtitle_preview(self):
        if not hasattr(self,"video_view"):
            return
        if not bool(getattr(self,"_subtitle_track_preview_visible",True)):
            self.video_view.subtitle_item.hide()
            return
        segments = self.current_translated_segments or self.current_segments
        index = int(getattr(self,"_selected_segment_index",-1))
        seconds=self._frame_time.value() if hasattr(self,"_frame_time") else 0
        item = segments[index] if 0 <= index < len(segments) else next((s for s in segments if float(s.get("start",0))<=seconds<float(s.get("end",0))),segments[0] if segments else {})
        self.video_view.subtitle_item.set_text(str(item.get("text",t("Subtitle preview"))))
        self.video_view.subtitle_item.show()
        self.video_view.reposition_subtitle()

    def preview_video(self):
        self.sync_live_subtitle_preview()
        if self._pipeline_active:
            self._pipeline_advance("preview")

    def _request_still(self):
        if self._frame_worker and self._frame_worker.isRunning():
            return
        video = self.video_path_edit.text().strip()
        if not video:
            return
        self._selected_segment_index=-1
        folder = Path(self.get_project_temp_dir("performance"))
        folder.mkdir(parents=True,exist_ok=True)
        worker = StillFrameWorker(video,str(folder/(uuid4().hex+".jpg")),self._frame_time.value(),self)
        self._frame_worker = worker
        self._frame_button.setEnabled(False)
        def ready(path,error):
            if getattr(self,"_closing",False):
                return
            self._frame_button.setEnabled(True)
            if path:
                self._still_time=float(worker.seconds)
                self.video_view.set_frame(path)
                self.update_subtitle_preview_style()
                self.refresh_timed_layer_preview()
                self._update_ocr_overlay()
            elif error:
                self.log("[Performance] "+error)
        worker.ready.connect(ready)
        worker.start()

    def load_video_project(self,video_path):
        self._run_deferred_startup_stage1()
        self._run_deferred_startup_stage2()
        super().load_video_project(video_path)
        self.sync_segment_editor_rows()
        self._refresh_layer_choices()
        self.refresh_timed_layer_preview()
        self._update_ocr_overlay()
        self._request_still()

    def exit_to_launcher(self):
        from services.processing_queue import editor_busy
        if editor_busy():
            QMessageBox.information(self,t("Processing"),t("Wait until processing has finished before leaving the editor."))
            return
        try:
            self.save_processing_profile()
        except Exception as exc:
            QMessageBox.critical(self,t("Error"),str(exc))
            return
        self._returning = True
        QApplication.setQuitOnLastWindowClosed(False)
        self.close()

    def closeEvent(self,event):
        if not getattr(self,"_returning",False):
            from services.processing_queue import editor_busy
            if editor_busy():
                event.ignore()
                QMessageBox.information(self,t("Processing"),t("Wait until processing has finished before leaving the editor."))
                return
            try:
                self.save_processing_profile()
            except Exception as exc:
                event.ignore()
                QMessageBox.critical(self,t("Error"),str(exc))
                return
        self._closing=True
        worker = getattr(self,"_frame_worker",None)
        if worker and worker.isRunning():
            if worker.process and worker.process.poll() is None:
                worker.process.terminate()
            worker.wait(35000)
        self._terminate_workers()
        if hasattr(self.video_view,"dispose_overlays"):
            self.video_view.dispose_overlays()
        for name in ("ocr_region_overlay","ocr_translator_overlay"):
            overlay=getattr(self,name,None)
            if overlay is not None:
                overlay.set_suppressed(True)
                overlay.hide()
        self.hide()
        event.accept()
        if getattr(self,"_returning",False):
            self.returned.emit()
        else:
            self.exit_requested.emit()


class QueueQualityEditor(VideoTranslatorGUI):
    """Full editor shares explicit return and application-close handling."""
    returned = Signal()
    exit_requested = Signal()
    save_processing_profile = PerformanceEditor.save_processing_profile
    exit_to_launcher = PerformanceEditor.exit_to_launcher
    closeEvent = PerformanceEditor.closeEvent
