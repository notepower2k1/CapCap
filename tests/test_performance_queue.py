"""Queue/Performance contracts, including isolated workers with synthetic media.

These tests never call AI providers: imported transcript + skip translation.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
for directory in (ROOT,ROOT/"app",ROOT/"ui"):
    if str(directory) not in sys.path:sys.path.insert(0,str(directory))
os.environ.setdefault("QT_QPA_PLATFORM","offscreen")
from PySide6.QtWidgets import QApplication,QDialog
from PySide6.QtCore import QTimer,QSettings,QCoreApplication,QEvent,Qt
import main_window
from performance_editor import PerformanceEditor,QueueQualityEditor,SubtitleTable
from services.project_service import ProjectService
from services.gui_project_bridge import GUIProjectBridge
from services import processing_queue as queue
from core.models import Segment
from views.processing_queue import QueuePanel
from worker_adapters.queue_workers import QueueRunner
from runtime_paths import bin_path,subprocess_hidden_kwargs


class PerformanceQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        cls.app._capcap_test_mode=True
        cls.folder=tempfile.TemporaryDirectory()
        cls.fixture=Path(cls.folder.name)/"fixture.mp4"
        ffmpeg=bin_path("ffmpeg","ffmpeg.exe")
        if not Path(ffmpeg).is_file():raise unittest.SkipTest("FFmpeg not installed")
        subprocess.run([ffmpeg,"-v","error","-y","-f","lavfi","-i","color=c=blue:s=320x180:d=1","-f","lavfi","-i","sine=frequency=440:duration=1","-c:v","libx264","-c:a","aac","-shortest",str(cls.fixture)],check=True,capture_output=True,**subprocess_hidden_kwargs())

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def setUp(self):
        self.workspace=tempfile.TemporaryDirectory()
        self.root=Path(self.workspace.name)
        self.service=ProjectService(str(self.root))
        self.windows=[]
        self.environment=dict(os.environ)

    def tearDown(self):
        queue.release("test")
        for window in self.windows:
            window.hide()
            if hasattr(window,"_terminate_workers"):window._terminate_workers()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.DeferredDelete)
        self.app.processEvents()
        os.environ.clear();os.environ.update(self.environment)
        self.workspace.cleanup()

    def editor(self,video,cls=PerformanceEditor):
        with patch.object(QTimer,"singleShot"),patch.object(main_window.VideoTranslatorGUI,"_preload_active_voice_if_needed"),patch.object(main_window.VideoTranslatorGUI,"_start_elevenlabs_catalog_refresh"),patch("views.launcher.LauncherWindow.add_recent"),patch.object(PerformanceEditor,"_request_still"):
            editor=cls()
            self.windows.append(editor)
            editor.workspace_root=str(self.root)
            editor.project_service=self.service
            editor.project_bridge=GUIProjectBridge(self.service)
            editor.settings=QSettings(str(self.root/"settings.ini"),QSettings.IniFormat)
            editor.ensure_media_backend_ready=lambda:None
            editor.schedule_timeline_visual_refresh=lambda **_:None
            editor.load_video_project(str(video))
        return editor

    def source(self,name="video.mp4"):
        video=self.root/name
        shutil.copyfile(self.fixture,video)
        return video

    def profile(self,video,config):
        state=self.service.ensure_project(str(video))
        state.settings.update(transcript_imported=True,processing_profile=copy.deepcopy(config),ui_work_mode="performance")
        self.service.save_segment_artifact(state,"transcript_segments","analysis/transcript_segments.json",[Segment(id=0,start=0,end=.8,original_text="Hello from CapCap")])
        self.service.save_project(state)
        return state

    def config(self,video):
        editor=self.editor(video)
        editor.save_processing_profile()
        config=copy.deepcopy(editor.current_project_state.settings["processing_profile"])
        config["prepare"].update(mode="subtitle",skip_translation=True,audio_handling_mode="fast")
        config["environment"]["CAPCAP_DEVICE"]="cpu"
        config["output_dir"]=str(self.root/"output")
        config["export"]["output_quality"]="source"
        editor.hide();editor._terminate_workers()
        return config

    def wait_queue(self,panel,timeout=45):
        deadline=time.monotonic()+timeout
        while panel.runner is not None and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.01)
        self.assertIsNone(panel.runner,"worker did not terminate")
        self.assertFalse(queue.busy())

    def test_store_deduplicates_recovers_interrupted_and_preserves_identity(self):
        video=self.source()
        store=queue.ProcessingQueue(self.root)
        store.add([str(video),str(video)])
        self.assertEqual(len(store.jobs),1)
        key=store.jobs[0]["id"]
        store.jobs[0]["status"]="running";store.save()
        restored=queue.ProcessingQueue(self.root)
        self.assertEqual(restored.jobs[0]["id"],key)
        self.assertEqual(restored.jobs[0]["status"],"interrupted")
        restored.retry(restored.jobs[0])
        self.assertEqual(restored.jobs[0]["status"],"pending")

    def test_completed_quality_project_cannot_be_added_to_performance(self):
        from services.project_work_mode import project_work_mode
        video=self.source()
        state=self.service.ensure_project(str(video))
        state.set_step_status("export","done")
        self.service.save_project(state)
        path=Path(self.service.project_file(state.project_root));before=path.read_bytes()
        store=queue.ProcessingQueue(self.root)
        self.assertEqual(project_work_mode(self.root,str(video)),"quality")
        self.assertEqual(store.add([str(video)]),[str(video)])
        self.assertFalse(store.jobs)
        self.assertEqual(path.read_bytes(),before)
        # Ownership is persistent, including after the queue/app is reopened.
        state.set_setting("ui_work_mode","quality");self.service.save_project(state)
        self.assertEqual(queue.ProcessingQueue(self.root).add([str(video)]),[str(video)])

    def test_quality_launcher_rejects_video_in_performance_queue(self):
        from types import SimpleNamespace
        from PySide6.QtWidgets import QComboBox
        from views import launcher as launcher_module
        from services.project_work_mode import project_work_mode
        video=self.source();store=queue.ProcessingQueue(self.root);store.add([str(video)])
        self.assertEqual(project_work_mode(self.root,str(video)),"performance")
        combo=QComboBox();combo.addItem("Quality","quality")
        fake=SimpleNamespace(selected_video=str(video),ui_mode_combo=combo)
        with patch.object(launcher_module,"workspace_root",return_value=str(self.root)), \
             patch("PySide6.QtWidgets.QMessageBox.information") as message:
            launcher_module.LauncherWindow.accept(fake)
        message.assert_called_once()

    def test_open_in_quality_transfers_project_and_removes_queue_entry(self):
        from services.project_work_mode import project_work_mode
        video=self.source();editor=self.editor(video);editor.save_processing_profile()
        identity=editor.current_project_state.project_uuid
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video)]);panel.refresh();panel.table.selectRow(0)
        def quality_editor(*args,**kwargs):
            return self.editor(video,QueueQualityEditor)
        with patch("editor_factory.create_editor",side_effect=quality_editor):
            panel.open_selected("quality")
        self.assertFalse(panel.store.jobs)
        self.assertEqual(project_work_mode(self.root,str(video)),"quality")
        self.assertEqual(panel.editor.current_project_state.project_uuid,identity)
        self.assertEqual(panel.store.add([str(video)]),[str(video)])

    def test_corrupt_queue_retains_backup(self):
        (self.root/"processing_queue.json").write_text("{invalid")
        store=queue.ProcessingQueue(self.root)
        self.assertFalse(store.jobs)
        self.assertTrue(list(self.root.glob("processing_queue.invalid-*.json")))

    def test_lock_blocks_all_editor_construction_and_wrong_owner_cannot_unlock(self):
        self.assertTrue(queue.acquire("test"))
        self.assertFalse(queue.acquire("other"))
        queue.release("other")
        self.assertTrue(queue.busy())
        with self.assertRaises(RuntimeError):main_window.VideoTranslatorGUI()
        queue.release("test")
        self.assertFalse(queue.busy())

    def test_project_save_failure_preserves_complete_previous_json(self):
        video=self.source();state=self.service.ensure_project(str(video))
        target=Path(self.service.project_file(state.project_root));before=target.read_bytes()
        def fail(value,handle,**kwargs):
            handle.write("{partial");raise OSError("disk full")
        with patch("services.project_service.json.dump",side_effect=fail),self.assertRaises(OSError):
            self.service.save_project(state)
        self.assertEqual(target.read_bytes(),before)
        self.assertFalse(list(target.parent.glob(".project-*.tmp")))

    def test_performance_has_no_scene_player_or_visual_workers_and_virtualizes_rows(self):
        editor=self.editor(self.source())
        self.assertIsNone(editor.timeline._scene)
        self.assertFalse(editor._media_backend_ready)
        self.assertEqual(editor.media_player.backend_name,"bootstrap")
        editor.current_segments=[dict(start=i,end=i+1,text=f"line {i}") for i in range(3000)]
        editor.sync_segment_editor_rows()
        self.assertEqual(editor._subtitle_table_model.rowCount(),3000)
        self.assertIs(editor._subtitle_table_model.segments,editor.current_segments)
        self.assertFalse(editor._segment_editor_rows)
        self.assertIsNone(editor._timeline_thumbnail_worker)
        self.assertIsNone(editor._timeline_waveform_worker)
        editor.update_subtitle_preview_style()
        self.assertTrue(editor.video_view.subtitle_item.current_text)

    def test_profile_round_trip_into_quality_preserves_layers_and_project_uuid(self):
        from app.layers.timeline import Track
        from app.layers.base import LayerType
        from app.layers.text import TextLayer
        video=self.source();editor=self.editor(video)
        editor.timeline._timeline.tracks.append(Track(name="T1 Text",type=LayerType.TEXT,layers=[TextLayer(text="Keep this",start=0,end=1)]))
        editor.current_segments=[dict(start=0,end=.8,text="Keep subtitle")]
        editor.persist_transcription_project_data(editor.current_segments)
        editor.subtitle_color_hex="#12AB34"
        editor.save_processing_profile()
        before=editor.current_project_state
        editor.hide();editor._terminate_workers()
        quality=self.editor(video,QueueQualityEditor)
        self.assertEqual(quality.current_project_state.project_uuid,before.project_uuid)
        self.assertEqual(quality.current_segments[0]["text"],"Keep subtitle")
        self.assertEqual(quality.subtitle_color_hex,"#12AB34")
        self.assertTrue(any(t.name=="T1 Text" for t in quality.timeline._timeline.tracks))

    def test_real_queue_is_sequential_continues_after_failure_and_exports(self):
        video=self.source();config=self.config(video)
        self.profile(video,config)
        broken=self.root/"broken.mp4";broken.write_bytes(b"not a video");self.profile(broken,config)
        other=self.source("other.mp4");self.profile(other,config)
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video),str(broken),str(other)]);panel.refresh()
        events=[]
        original=panel._update
        def update(job,status,message):
            events.append((job,status));original(job,status,message)
        panel._update=update
        panel.auto_export_checkbox.setChecked(True)
        panel.start()
        self.assertTrue(queue.busy())
        self.wait_queue(panel)
        self.assertEqual([j["status"] for j in panel.store.jobs],["completed","failed","completed"])
        first,last=panel.store.jobs[0],panel.store.jobs[-1]
        self.assertLess(events.index((first["id"],"completed")),events.index((last["id"],"running")))
        self.assertTrue(Path(first["output"]).is_file())
        self.assertTrue(Path(last["output"]).is_file())

    def test_processing_waits_for_manual_multi_export_and_keeps_pending_jobs(self):
        from PySide6.QtCore import QItemSelectionModel
        video=self.source();config=self.config(video);self.profile(video,config)
        other=self.source("other.mp4");self.profile(other,config)
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video),str(other)]);panel.refresh()
        self.assertFalse(panel.auto_export_checkbox.isChecked())
        panel.start();self.wait_queue(panel)
        self.assertEqual([j["status"] for j in panel.store.jobs],["processed","processed"])
        self.assertFalse(list((self.root/"output").glob("*.mp4")))
        persisted=queue.ProcessingQueue(self.root)
        self.assertFalse(persisted.auto_export)
        self.assertEqual(persisted.jobs[0]["status"],"processed")
        third=self.source("pending.mp4");self.profile(third,config)
        panel.store.add([str(third)]);panel.refresh();panel.table.clearSelection()
        for row in (0,1):
            panel.table.selectionModel().select(panel.table.model().index(row,0),QItemSelectionModel.Select|QItemSelectionModel.Rows)
        self.assertTrue(panel.buttons["Export selected videos"].isEnabled())
        panel.export_selected();self.wait_queue(panel)
        self.assertEqual([j["status"] for j in panel.store.jobs],["completed","completed","pending"])
        for job in panel.store.jobs[:2]:self.assertTrue(Path(job["output"]).is_file())

    def test_export_only_reuses_tts_without_prepare_or_voice_calls(self):
        import wave
        from queue_worker import run_job
        from services.workflow_runtime import WorkflowRuntime
        video=self.source();config=self.config(video);self.profile(video,config)
        with patch.object(WorkflowRuntime,"run_export",side_effect=AssertionError("Processing must not render")):
            result=run_job(str(self.root),str(video),config,lambda *_:None,task="process")
        self.assertEqual(result["status"],"processed")
        self.assertFalse(result["output"])
        state=self.service.load_project(result["project"])
        voice=Path(state.project_root)/"audio"/"existing-voice.wav"
        with wave.open(str(voice),"wb") as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
            audio.writeframes(b"\0\0"*16000)
        state.set_artifact("voice_vi",str(voice));state.set_step_status("generate_tts","done")
        self.service.save_project(state)
        config["prepare"]["mode"]="both"
        with patch.object(WorkflowRuntime,"run_prepare",side_effect=AssertionError("Export must not prepare/transcribe/translate")), \
             patch.object(WorkflowRuntime,"run_voice",side_effect=AssertionError("Export must reuse TTS")):
            exported=run_job(str(self.root),str(video),config,lambda *_:None,task="export")
        self.assertTrue(Path(exported["output"]).is_file())
        self.assertEqual(exported["status"],"completed")

    def test_stop_after_current_also_works_without_rendering(self):
        video=self.source();config=self.config(video);self.profile(video,config)
        other=self.source("next.mp4");self.profile(other,config)
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video),str(other)]);panel.refresh()
        panel.start();panel.stop_after();self.wait_queue(panel)
        self.assertEqual([j["status"] for j in panel.store.jobs],["processed","pending"])
        self.assertFalse(list((self.root/"output").glob("*.mp4")))

    def test_stop_now_waits_for_owned_process_before_unlock(self):
        video=self.source();config=self.config(video);self.profile(video,config)
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video)]);panel.refresh()
        original_popen=subprocess.Popen
        def sleeper(command,**kwargs):
            if command[0]!=sys.executable:
                return original_popen(command,**kwargs)
            return original_popen([sys.executable,"-u","-c","import time;print('waiting',flush=True);time.sleep(40)"],**kwargs)
        with patch("worker_adapters.queue_workers.subprocess.Popen",side_effect=sleeper):
            panel.start()
            deadline=time.monotonic()+5
            while panel.runner.process is None and time.monotonic()<deadline:
                self.app.processEvents();time.sleep(.01)
            process=panel.runner.process
            self.assertIsNotNone(process)
            self.assertTrue(queue.busy())
            panel.stop_now()
            self.wait_queue(panel)
        self.assertIsNotNone(process.poll())
        self.assertEqual(panel.store.jobs[0]["status"],"interrupted")

    def test_stop_after_current_keeps_next_video_pending(self):
        video=self.source();config=self.config(video);self.profile(video,config)
        other=self.source("next.mp4");self.profile(other,config)
        launcher=QDialog();self.windows.append(launcher)
        panel=QueuePanel(str(self.root),launcher)
        panel.store.add([str(video),str(other)]);panel.refresh()
        panel.auto_export_checkbox.setChecked(True)
        panel.start();panel.stop_after()
        self.wait_queue(panel)
        self.assertEqual([j["status"] for j in panel.store.jobs],["completed","pending"])

    def test_opening_another_profile_does_not_change_first_projects_config(self):
        video=self.source();config=self.config(video)
        config["prepare"]["target_language"]="vi"
        config["controls"]["lang_target_combo"]={"data":"vi","text":"Vietnamese"}
        first=self.profile(video,config)
        before=copy.deepcopy(first.settings["processing_profile"])
        other=self.source("other.mp4")
        second=copy.deepcopy(config)
        second["prepare"]["target_language"]="en"
        second["controls"]["lang_target_combo"]={"data":"en","text":"English"}
        second["environment"]["CAPCAP_OMNIVOICE_STEPS"]="16"
        self.profile(other,second)
        editor=self.editor(other)
        self.assertEqual(editor.get_target_language_code(),"en")
        self.assertEqual(editor.omnivoice_quality_combo.currentData(),16)
        self.assertEqual(self.service.load_project(self.service.project_file(first.project_root)).settings["processing_profile"],before)

    def test_grouped_tts_keeps_cue_times_and_audio_references(self):
        from services.voice_edit_service import group_voice_segments,merge_generated_voice_segments
        source=[dict(start=0,end=.4,text="First",tts_group_id="g"),dict(start=.4,end=.8,text="Second",tts_group_id="g")]
        grouped=group_voice_segments(source)
        self.assertEqual(len(grouped),1)
        self.assertEqual(grouped[0]["text"],"First Second")
        generated=[dict(grouped[0],_wav_path="speech.wav",_audio_end=1,voice_speed=1.1)]
        merge_generated_voice_segments(source,generated)
        self.assertEqual([(s["start"],s["end"]) for s in source],[(0,.4),(.4,.8)])
        self.assertTrue(all(s["_wav_path"]=="speech.wav" for s in source))
        self.assertTrue(all(s["voice_speed"]==1.1 for s in source))

    def test_launcher_survives_opening_and_returning_from_performance_editor(self):
        self._check_launcher_editor_lifecycle()

    def test_editor_close_also_finishes_hidden_launcher(self):
        self._check_launcher_editor_lifecycle(close_application=True)

    def _check_launcher_editor_lifecycle(self,close_application=False):
        from views import launcher as launcher_module
        video=self.source()
        errors=[]
        finished=[]
        real_launcher=launcher_module.LauncherWindow

        def create_editor(*args,**kwargs):
            editor=PerformanceEditor()
            self.windows.append(editor)
            editor.workspace_root=str(self.root)
            editor.project_service=self.service
            editor.project_bridge=GUIProjectBridge(self.service)
            editor.settings=QSettings(str(self.root/"settings.ini"),QSettings.IniFormat)
            editor.returned.connect(kwargs["on_return"])
            editor.returned.connect(lambda:self.windows.remove(editor))
            editor.exit_requested.connect(kwargs["on_close"])
            editor.exit_requested.connect(lambda:self.windows.remove(editor))
            return editor

        def create_launcher():
            window=real_launcher()
            panel=window._queue_panel
            panel.store.add([str(video)]);panel.refresh();panel.table.selectRow(0)
            window.finished.connect(lambda *_:finished.append(True))
            # Context-bound timers cannot fire on the next test's event loop.
            timer=QTimer(window);timer.setSingleShot(True)
            timer.timeout.connect(window.reject);timer.start(15000)

            def returned():
                try:
                    self.assertTrue(window.isVisible())
                    self.assertFalse(finished)
                    self.assertIsNone(panel.editor)
                    self.assertIsNotNone(self.service.load_project(
                        self.service.project_file(self.service.projects_root+"/"+self.service._build_project_id(str(video)))
                    ).settings.get("processing_profile"))
                except Exception as exc:errors.append(exc)
                finally:window.reject()

            def opened():
                try:
                    self.assertIsNotNone(panel.editor)
                    self.assertTrue(panel.editor.isVisible())
                    self.assertFalse(window.isVisible())
                    self.assertFalse(finished)
                    if close_application:
                        panel.editor.close()
                        self.assertTrue(finished)
                        self.assertIsNone(panel.editor)
                    else:
                        panel.editor.exit_to_launcher()
                        QTimer.singleShot(50,returned)
                except Exception as exc:
                    errors.append(exc);window.reject()

            def open_editor():
                try:
                    self.assertTrue(hasattr(window,"loading_container"))
                    panel.open_selected()
                    QTimer.singleShot(150,opened)
                except Exception as exc:
                    errors.append(exc);window.reject()
            QTimer.singleShot(0,open_editor)
            return window

        with patch.object(launcher_module,"workspace_root",return_value=str(self.root)), \
             patch.object(real_launcher,"_load_recent"), \
             patch.object(real_launcher,"_validate_resources_for_device"), \
             patch.object(launcher_module,"LauncherWindow",side_effect=create_launcher), \
             patch("editor_factory.create_editor",side_effect=create_editor), \
             patch.object(PerformanceEditor,"_start_elevenlabs_catalog_refresh"):
            self.assertEqual(launcher_module.show_launcher(None),"")
        if errors:raise errors[0]
        self.assertTrue(finished,"Launcher returned before its explicit close")

    def test_closing_standalone_editor_exits_process_with_frame_worker_stopped(self):
        video=self.source()
        script='''
import os,sys
from pathlib import Path
root=Path(sys.argv[1])
sys.path[:0]=[str(root/'ui'),str(root/'app'),str(root)]
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer,QSettings
from performance_editor import PerformanceEditor
from editor_factory import create_editor
from services.project_service import ProjectService
from services.gui_project_bridge import GUIProjectBridge
app=QApplication([])
app.setQuitOnLastWindowClosed(False)
with patch.object(PerformanceEditor,'_start_elevenlabs_catalog_refresh'):
    editor=create_editor('performance')
    editor.workspace_root=sys.argv[2]
    editor.project_service=ProjectService(sys.argv[2])
    editor.project_bridge=GUIProjectBridge(editor.project_service)
    editor.settings=QSettings(str(Path(sys.argv[2])/'settings.ini'),QSettings.IniFormat)
    editor.load_video_project(sys.argv[3])
    QTimer.singleShot(300,editor,editor.close)
    app.exec()
assert editor._closing
assert not editor._frame_worker.isRunning()
print('EDITOR_PROCESS_EXITED_CLEANLY',flush=True)
'''
        env=dict(os.environ,QT_QPA_PLATFORM="offscreen",PYTHONUTF8="1",PYTHONIOENCODING="utf-8")
        result=subprocess.run([sys.executable,"-u","-c",script,str(ROOT),str(self.root),str(video)],
            capture_output=True,text=True,encoding="utf-8",env=env,timeout=25,**subprocess_hidden_kwargs())
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("EDITOR_PROCESS_EXITED_CLEANLY",result.stdout)

    def test_static_layer_editing_round_trips_into_quality(self):
        from PySide6.QtGui import QPixmap,QColor
        from PySide6.QtCore import QRectF
        video=self.source();editor=self.editor(video)
        logo=self.root/"logo.png"
        pixmap=QPixmap(32,32);pixmap.fill(QColor("red"));pixmap.save(str(logo))
        editor.video_view.set_frame(str(logo))
        self.assertFalse(editor.add_layer_btn.isHidden())
        self.assertFalse(editor.subtitle_inspector_shell.isHidden())
        self.assertFalse(editor.video_view.video_surface.testAttribute(Qt.WA_NativeWindow))
        editor.on_add_timeline_layer("text")
        editor.on_add_timeline_layer("blur")
        editor.on_add_timeline_layer("mask")
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",return_value=(str(logo),"")):
            editor.on_add_timeline_layer("logo")
        import wave
        music=self.root/"music.wav"
        with wave.open(str(music),"wb") as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000)
            audio.writeframes(b"\0\0"*16000)
        with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",return_value=(str(music),"")):
            editor.add_music_layer()
        model=editor.timeline._timeline
        kinds={str(layer.type.value):layer for track in model.tracks for layer in track.layers}
        self.assertTrue({"text","blur","mask","image"}.issubset(kinds))
        text,blur,mask=kinds["text"],kinds["blur"],kinds["mask"]
        editor.video_view.textLayerMoved.emit(text.id,.25,.35)
        editor._on_mask_moved(mask,.15,.2,.3,.25)
        editor.timeline.select_layer(blur.id);editor.on_timeline_layer_selected(blur.id)
        editor.video_view.blur_overlay._regions=[QRectF(.12,.14,.5,.2)]
        editor.video_view.blurRegionChanged.emit()
        editor.blur_inspector_radius_slider.setValue(12)
        editor.save_processing_profile()
        editor.hide();editor.video_view.dispose_overlays();editor._terminate_workers()
        quality=self.editor(video,QueueQualityEditor)
        restored={str(layer.type.value):layer for track in quality.timeline._timeline.tracks for layer in track.layers}
        self.assertAlmostEqual(restored["text"].transform.x,.25)
        self.assertAlmostEqual(restored["blur"].position_x,.12)
        self.assertAlmostEqual(restored["blur"].blur_strength,12)
        self.assertAlmostEqual(restored["mask"].position_x,.15)
        self.assertEqual(restored["image"].source,str(logo))
        self.assertTrue(any(track.name=="A2 Music" and track.layers[0].source==str(music)
                            for track in quality.timeline._timeline.tracks))

    def test_deleting_optional_layer_does_not_reappear_on_reopen(self):
        video=self.source();editor=self.editor(video)
        editor.on_add_timeline_layer("text")
        text_id=editor.timeline._selected_layer_id
        index=editor._layer_choices.findData(text_id)
        editor._layer_choices.setCurrentIndex(index)
        editor._delete_layer_choice()
        editor.save_processing_profile();editor.hide()
        reopened=self.editor(video)
        self.assertFalse(any(layer.id==text_id for track in reopened.timeline._timeline.tracks for layer in track.layers))

    def test_editing_active_blur_preserves_inactive_layer(self):
        from PySide6.QtCore import QRectF
        editor=self.editor(self.source())
        editor.on_add_timeline_layer("blur")
        editor.on_add_timeline_layer("blur")
        track=next(track for track in editor.timeline._timeline.tracks if track.name=="B1")
        first,second=track.layers
        first.start,first.end=0,.4
        second.start,second.end=.6,1
        second.position_x=.55
        editor.timeline.select_layer(first.id);editor.on_timeline_layer_selected(first.id)
        editor.video_view.blur_overlay._regions=[QRectF(.1,.2,.3,.15)]
        editor.video_view.blurRegionChanged.emit()
        editor.save_processing_profile()
        self.assertEqual(len(track.layers),2)
        self.assertAlmostEqual(first.position_x,.1)
        self.assertAlmostEqual(second.position_x,.55)
        saved=editor.current_project_state.settings["blur_state"]["regions"]
        self.assertEqual(len(saved),2)
        self.assertAlmostEqual(saved[1]["start"],.6)

    def test_ocr_toolbar_and_crop_are_restored_per_video(self):
        from PySide6.QtCore import QRectF
        from services.processing_queue import ENV_KEYS
        first=self.source();editor=self.editor(first)
        editor.set_project_transcription_engine("ocr");editor.refresh_ui_state()
        self.assertFalse(editor.preview_blur_group_widget.isHidden())
        for button in (editor.blur_add_btn,editor.add_logo_btn,editor.add_mask_btn,editor.add_text_btn,editor.ocr_region_btn):
            self.assertFalse(button.isHidden())
        self.assertTrue(editor.ocr_translator_btn.isHidden())
        self.assertTrue(editor.ocr_region_overlay.isVisible())
        editor.ocr_region_overlay._norm=QRectF(.1,.6,.75,.2)
        editor.ocr_region_overlay._save_rect()
        editor.save_processing_profile()
        crop=editor.current_project_state.settings["processing_profile"]["environment"]["OCR_SUBTITLE_RECT"]
        self.assertIn("OCR_SUBTITLE_RECT",ENV_KEYS)
        editor.ocr_region_btn.click()
        self.assertFalse(editor.ocr_region_overlay.isVisible())
        editor.ocr_region_btn.click()
        self.assertTrue(editor.ocr_region_overlay.isVisible())
        editor.hide()
        second=self.editor(self.source("second-ocr.mp4"))
        second.ocr_region_overlay._norm=QRectF(0,.8,1,.15)
        second.ocr_region_overlay._save_rect();second.save_processing_profile();second.hide()
        reopened=self.editor(first)
        self.assertEqual(os.environ["OCR_SUBTITLE_RECT"],crop)
        self.assertAlmostEqual(reopened.ocr_region_overlay._norm.x(),.1)
        self.assertAlmostEqual(reopened.ocr_region_overlay._norm.y(),.6)
        self.assertAlmostEqual(reopened.ocr_region_overlay._norm.width(),.75)
        self.assertAlmostEqual(reopened.ocr_region_overlay._norm.height(),.2)
        self.assertTrue(reopened.ocr_translator_btn.isHidden())

    def test_ocr_crop_receives_mouse_drag_and_resize_on_still_preview(self):
        from PySide6.QtCore import QPoint,QRectF
        from PySide6.QtTest import QTest
        editor=self.editor(self.source());editor.resize(1440,900)
        editor.set_project_transcription_engine("ocr")
        self.app.processEvents()
        overlay=editor.ocr_region_overlay
        overlay._norm=QRectF(.1,.6,.7,.2);overlay._save_rect();overlay.sync_to_view()
        self.assertFalse(overlay.isWindow())
        self.assertFalse(bool(overlay.windowFlags() & Qt.WindowTransparentForInput))
        editor.ocr_region_btn.click();editor.ocr_region_btn.click()
        self.assertFalse(bool(overlay.windowFlags() & Qt.WindowTransparentForInput))
        self.assertEqual(overlay.geometry(),editor.video_view.rect())
        start=overlay._ocr_rect().center().toPoint()
        receiver=self.app.widgetAt(overlay.mapToGlobal(start))
        self.assertIs(receiver,overlay)
        QTest.mousePress(receiver,Qt.LeftButton,Qt.NoModifier,start)
        QTest.mouseMove(receiver,start+QPoint(0,-20),20)
        QTest.mouseRelease(receiver,Qt.LeftButton,Qt.NoModifier,start+QPoint(0,-20))
        self.assertLess(overlay._norm.y(),.6)
        before=overlay._norm.width()
        corner=overlay._ocr_rect().bottomRight().toPoint()
        QTest.mousePress(overlay,Qt.LeftButton,Qt.NoModifier,corner)
        QTest.mouseMove(overlay,corner+QPoint(-20,-5),20)
        QTest.mouseRelease(overlay,Qt.LeftButton,Qt.NoModifier,corner+QPoint(-20,-5))
        self.assertLess(overlay._norm.width(),before)
        editor.save_processing_profile()
        crop=editor.current_project_state.settings["processing_profile"]["environment"]["OCR_SUBTITLE_RECT"]
        self.assertAlmostEqual(float(crop.split(",")[1]),overlay._norm.y(),places=3)

    def test_ocr_overlay_keeps_frame_visible_outside_selection(self):
        from PySide6.QtCore import QPoint,QRectF
        from PySide6.QtGui import QColor,QPixmap
        editor=self.editor(self.source());editor.resize(1440,900)
        self.app.processEvents()
        frame=QPixmap(320,180);frame.fill(QColor('#b44a32'))
        image=self.root/'still.png';frame.save(str(image))
        editor.video_view.set_frame(str(image))
        editor.set_project_transcription_engine('ocr')
        overlay=editor.ocr_region_overlay
        overlay._norm=QRectF(.1,.6,.7,.2);overlay._save_rect()
        editor.ocr_region_btn.click()
        self.app.processEvents()
        rect=editor.video_view.get_video_content_rect()
        point=QPoint(round(rect.left()+rect.width()*.5),round(rect.top()+rect.height()*.25))
        before=editor.video_view.grab().toImage().pixelColor(point)
        self.assertGreater(before.red(),100)
        editor.ocr_region_btn.click();self.app.processEvents()
        self.assertTrue(overlay.isVisible())
        after=editor.video_view.grab().toImage().pixelColor(point)
        self.assertEqual(after,before)
        inside=overlay._ocr_rect().center().toPoint()
        tinted=editor.video_view.grab().toImage().pixelColor(inside)
        self.assertEqual(tinted.alpha(),255)
        self.assertGreater(tinted.red(),100)
        from PySide6.QtTest import QTest
        QTest.mousePress(overlay,Qt.LeftButton,Qt.NoModifier,inside)
        QTest.mouseMove(overlay,inside+QPoint(0,-15),20)
        QTest.mouseRelease(overlay,Qt.LeftButton,Qt.NoModifier,inside+QPoint(0,-15))
        self.app.processEvents()
        self.assertEqual(editor.video_view.grab().toImage().pixelColor(point),before)


if __name__=="__main__":unittest.main()
