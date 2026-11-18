"""Layout geometry, real FFmpeg composition and editor project contracts."""
import copy
import contextlib
import io
import re
import subprocess
import unittest
from pathlib import Path
import test_performance_queue as harness
from app.video_layout import config, geometry, map_point, canvas_point, filter_chain
from runtime_paths import bin_path


class VideoLayoutTests(unittest.TestCase):
    def test_covered_background_skips_compositing_but_retains_fill_crop(self):
        cfg = dict(template="free", background="image", image="unused.png")
        self.assertEqual(filter_chain(cfg, 1920, 1080, 1920, 1080), "setsar=1")
        fill = filter_chain(cfg, 1080, 1920, 1920, 1080, "fill")
        self.assertIn("crop=1080:1920", fill)
        self.assertNotIn("movie=", fill)
        self.assertIn("movie=", filter_chain(cfg, 1080, 1920, 1920, 1080))
    def test_static_image_mask_shadow_preserve_source_frame_times(self):
        ffmpeg=bin_path('ffmpeg','ffmpeg.exe')
        for fps in (24,25,30,60):
            for background in ('color','image','blur'):
                cfg=dict(template='frame',background=background,image=str(harness.ROOT/'assets/capcap.png'),
                         corner=12,shadow=8,video_height=80)
                graph=filter_chain(cfg,180,320,320,180)
                p=subprocess.run([ffmpeg,'-hide_banner','-loglevel','info','-f','lavfi','-i',
                    f'testsrc2=s=320x180:r={fps}:d=1','-vf',graph+',showinfo',
                    '-fps_mode','passthrough','-f','null','-'],capture_output=True,timeout=20)
                stderr=p.stderr.decode(errors='replace')
                self.assertEqual(p.returncode,0,stderr[-2000:])
                times=[float(t) for t in re.findall(r'\bn:\s*\d+\s+pts:\s*-?\d+\s+pts_time:(\S+)',stderr)]
                self.assertEqual(len(times),fps,(background,fps,times))
                for index,time in enumerate(times):
                    self.assertAlmostEqual(time,index/fps,places=5)
                self.assertNotIn('non monotonically increasing',stderr)

    def test_native_graph_composes_source_filters_before_layout(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from ui.utils.media_backend import MpvMediaPlayerBackend as Backend
        cfg=config(dict(template='hook',background='blur',corner=10))
        cfg.update(_canvas_width=180,_canvas_height=320,_source_width=320,_source_height=180,
                   _mode='fit',_focus=(.5,.5))
        for color,blur,mask in ((False,False,False),(True,False,False),(False,True,False),(False,False,True),(True,True,True)):
            backend=SimpleNamespace(_is_closed=False,_color_filter_state={'video_layout':cfg,'final':{'brightness':10 if color else 0}},
                _blur_region=[dict(x=.2,y=.2,width=.5,height=.5,blur_strength=4)] if blur else [],
                _mask_region=[dict(x=.1,y=.1,width=.1,height=.1,color='#FF0000')] if mask else [],
                video_view=SimpleNamespace(video_source_width=320,video_source_height=180),
                _player=MagicMock(),_managed_vf_label='test')
            backend._build_blur_body=lambda **kw:Backend._build_blur_body(backend,**kw)
            backend._build_mask_body=lambda **kw:Backend._build_mask_body(backend,**kw)
            Backend._apply_all_filters(backend)
            graph=backend._last_applied_vf_combined
            p=subprocess.run([bin_path('ffmpeg','ffmpeg.exe'),'-v','error','-f','lavfi','-i',
                'color=c=blue:s=320x180:d=0.2','-vf',graph,'-frames:v','1','-f','null','-'],capture_output=True,timeout=15)
            self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'))

    def test_geometry_round_trip_across_ratios(self):
        for canvas in ((320,180),(180,320),(400,400),(400,300)):
            for mode in ('fit','fill'):
                for point in ((.5,.5),(-.3,1.8)):
                    mapped = map_point(*point, {}, (320,180), canvas, mode, (.2,.7))
                    restored = canvas_point(*mapped, {}, (320,180), canvas, mode, (.2,.7))
                    for a,b in zip(point,restored): self.assertAlmostEqual(a,b)

    def test_ffmpeg_background_corner_shadow(self):
        ffmpeg = bin_path('ffmpeg','ffmpeg.exe')
        for background in ('color','blur','image'):
            for mode in ('fit','fill'):
                cfg = dict(template='hook',background=background,corner=12,shadow=8,
                           image=str(harness.ROOT/'assets/capcap.png'))
                graph = filter_chain(cfg,180,320,320,180,mode)
                p = subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','color=c=blue:s=320x180:d=0.2',
                    '-vf',graph,'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,timeout=30)
                self.assertEqual(p.returncode,0,p.stderr.decode(errors='replace'))
                self.assertEqual(len(p.stdout),180*320*3)
                center = (160*180+90)*3
                self.assertGreater(p.stdout[center+2],200)
                if background == 'color':
                    self.assertLess(p.stdout[2],100)


class VideoLayoutUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): harness.PerformanceQueueTests.setUpClass()
    @classmethod
    def tearDownClass(cls): harness.PerformanceQueueTests.tearDownClass()
    def setUp(self):
        self.host=harness.PerformanceQueueTests(); self.host.setUp()
    def tearDown(self): self.host.tearDown()

    def test_cinematic_and_blank_templates_do_not_leave_empty_text_tracks(self):
        from app.layers.base import LayerType
        from app.layers.timeline import Track
        from app.layers.text import TextLayer
        gui=self.host.editor(self.host.source())
        model=gui.timeline._timeline
        # Include a saved empty T1 from builds that created it unconditionally.
        model.tracks.append(Track(name='T1 Text',type=LayerType.TEXT))
        layout=gui.video_layout_controller
        layout.templates.setCurrentRow(3);layout.apply()
        self.assertFalse(any(track.type==LayerType.TEXT for track in model.tracks))
        layout.templates.setCurrentRow(1);layout.fields['hook'].setText('');layout.apply()
        self.assertFalse(any(track.type==LayerType.TEXT for track in model.tracks))
        layout.fields['hook'].setText('A hook');layout.apply()
        self.assertTrue(any(track.type==LayerType.TEXT for track in model.tracks))
        layout.templates.setCurrentRow(3);layout.apply()
        self.assertFalse(any(track.type==LayerType.TEXT for track in model.tracks))
        # Existing user text remains intact when the template has no text.
        user=TextLayer(text='Keep me',start=0,end=1)
        model.tracks.append(Track(name='T1 Text',type=LayerType.TEXT,layers=[user]))
        layout.apply()
        self.assertIn(user.id,[layer.id for track in model.tracks for layer in track.layers])

    def test_repeated_cinematic_image_apply_keeps_one_background_and_restores_history(self):
        from PySide6.QtGui import QImage, QColor
        gui=self.host.editor(self.host.source())
        gui.video_view.set_frame(str(harness.ROOT/'assets/preview.JPG'))
        controller=gui.video_layout_controller
        controller.templates.setCurrentRow(3)
        controller.background.setCurrentIndex(controller.background.findData('image'))
        paths=[]
        for index in range(12):
            image=QImage(128,72,QImage.Format_RGB32);image.fill(QColor.fromHsv(index*25,180,170))
            path=self.host.root/f'background_{index}.jpg';self.assertTrue(image.save(str(path)))
            paths.append(str(path));controller.image.setText(str(path));controller.apply()
            layers=[layer for track in gui.timeline._timeline.tracks for layer in track.layers if layer.type.value=='background']
            self.assertEqual(len(layers),1)
            self.assertEqual(layers[0].source,str(path))
            self.assertTrue(controller.apply_btn.isEnabled())
        controller.undo();self.assertEqual(controller.current()['image'],paths[-2])
        controller.redo();self.assertEqual(controller.current()['image'],paths[-1])

    def test_template_ratio_round_trip_and_undo_keep_user_edits(self):
        from app.layers.text import TextLayer
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        gui=self.host.editor(self.host.source())
        ratio=gui.output_ratio_combo
        ratio.setCurrentIndex(ratio.findData('16:9'))
        c=gui.video_layout_controller
        c.templates.setCurrentRow(1);c.fields['hook'].setText('Hook');c.apply()
        layer=next(l for tr in gui.timeline._timeline.tracks for l in tr.layers if l.id=='layout-hook')
        before=copy.deepcopy(layer.to_dict())
        ratio.setCurrentIndex(ratio.findData('9:16'))
        ratio.setCurrentIndex(ratio.findData('16:9'))
        self.assertEqual(layer.font_size,before['font_size'])
        self.assertAlmostEqual(layer.transform.y,before['transform']['y'])
        user=TextLayer(text='Added after template',start=0,end=1)
        find_or_create_track(gui.timeline._timeline,'T1 Text',LayerType.TEXT,80).layers.append(user)
        user.text='Edited'
        c.reset();c.undo()
        self.assertIn(user.id,[l.id for tr in gui.timeline._timeline.tracks for l in tr.layers])
        self.assertEqual(user.text,'Edited')

    def test_queue_export_uses_applied_layout(self):
        from queue_worker import run_job
        from video_processor import get_video_dimensions
        video=self.host.source()
        gui=self.host.editor(video)
        gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData('9:16'))
        c=gui.video_layout_controller
        c.templates.setCurrentRow(1);c.fields['hook'].setText('Queue hook');c.apply()
        gui.save_processing_profile()
        cfg=copy.deepcopy(gui.current_project_state.settings['processing_profile'])
        cfg['prepare'].update(mode='subtitle',skip_translation=True,audio_handling_mode='fast')
        cfg['output_dir']=str(self.host.root/'queue_output')
        self.host.profile(video,cfg)
        with contextlib.redirect_stdout(io.StringIO()):
            result=run_job(str(self.host.root),str(video),cfg,lambda *_:None)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(get_video_dimensions(result['output']),(100,180))

    def test_resource_package_keeps_background_ownership_and_relinks_image(self):
        import json
        from ui.controllers.resource_export_controller import capture_resources
        from app.services.resource_package import build_catalog, export_resources, import_resources
        gui=self.host.editor(self.host.source())
        c=gui.video_layout_controller
        c.templates.setCurrentRow(3)
        c.background.setCurrentIndex(c.background.findData('image'))
        c.image.setText(str(harness.ROOT/'assets/capcap.png'));c.apply()
        snapshot=capture_resources(gui)
        items=build_catalog(snapshot)
        package=self.host.root/'layout_package'
        export_resources(snapshot,items,[i['id'] for i in items],package)
        result=import_resources(package,self.host.root/'imported')
        state=json.loads((Path(result['path'])/'project.json').read_text(encoding='utf-8'))
        self.assertEqual(state['settings']['video_layout']['template'],'frame')
        timeline=json.loads(Path(state['artifacts']['timeline']).read_text(encoding='utf-8'))
        background=next(layer for track in timeline['tracks'] for layer in track['layers'] if layer['type']=='background')
        self.assertEqual(background['metadata']['layout_role'],'background')
        self.assertTrue(Path(background['source']).is_file())
        self.assertNotEqual(background['source'],str(harness.ROOT/'assets/capcap.png'))

    def test_apply_draft_reapply_undo_reset_restore_isolation(self):
        from app.layers.text import TextLayer
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        gui=self.host.editor(self.host.source())
        gui.current_segments=[dict(start=0,end=.8,text='Hello')]
        gui.apply_segments_to_timeline()
        layout=gui.video_layout_controller
        user=TextLayer(text='My layer',start=0,end=1)
        find_or_create_track(gui.timeline._timeline,'T1 Text',LayerType.TEXT,80).layers.append(user)
        before=copy.deepcopy(gui.timeline._timeline.to_dict())
        layout.templates.setCurrentRow(1); layout.fields['hook'].setText('A hook')
        self.assertEqual(before,gui.timeline._timeline.to_dict())
        layout.apply()
        self.assertEqual(layout.current()['template'],'hook')
        self.assertEqual(gui.get_video_filter_state()['video_layout']['template'],'hook')
        layout.apply()
        layers=[l for tr in gui.timeline._timeline.tracks for l in tr.layers]
        self.assertEqual(sum(l.id=='layout-hook' for l in layers),1)
        self.assertIn(user.id,[l.id for l in layers])
        state=gui.current_project_state
        gui.load_project_context(state)
        self.assertEqual(layout.current()['template'],'hook')
        layout.reset()
        self.assertIsNone(layout.current())
        self.assertIn(user.id,[l.id for tr in gui.timeline._timeline.tracks for l in tr.layers])
        layout.undo(); self.assertEqual(layout.current()['template'],'hook')
        layout.redo(); self.assertIsNone(layout.current())
        other=self.host.editor(self.host.source('other.mp4'))
        self.assertIsNone(other.video_layout_controller.current())

    def test_real_export_fast_preview_and_capcut_preserve_layout(self):
        from app.workflows.export_workflow import ExportWorkflow
        from app.capcut.draft_export import DraftExporter
        from ui.controllers.capcut_export_controller import capture_snapshot
        from video_processor import get_video_dimensions
        from preview_processor import mux_audio_into_video_for_preview as mux_audio_for_preview
        import json
        gui=self.host.editor(self.host.source())
        gui.current_segments=[dict(start=0,end=.8,text='Hello')]
        gui.apply_segments_to_timeline()
        gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData('9:16'))
        c=gui.video_layout_controller
        c.templates.setCurrentRow(1);c.fields['hook'].setText('Hook');c.apply()
        c.diagram.grab()  # Exercise the actual Qt paint path.
        gui.video_view.set_frame(str(harness.ROOT/'assets/capcap.png'))
        srt=self.host.root/'test.srt';srt.write_text('1\n00:00:00,000 --> 00:00:00,800\nHello\n',encoding='utf-8')
        out=self.host.root/'layout.mp4'
        with contextlib.redirect_stdout(io.StringIO()):
            ExportWorkflow(str(self.host.root)).run(video_path=gui.video_path_edit.text(),output_path=str(out),
                mode='subtitle',srt_path=str(srt),subtitle_style=gui.get_subtitle_export_style(),
                output_ratio='9:16',video_filter_state=gui.get_video_filter_state(),
                project_state_path=self.host.service.project_file(gui.current_project_state.project_root),
                project_temp_dir=str(self.host.root/'render'))
        self.assertEqual(get_video_dimensions(str(out)),(100,180))
        pixels=subprocess.run([bin_path('ffmpeg','ffmpeg.exe'),'-v','error','-i',str(out),'-frames:v','1',
            '-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True).stdout
        # Hook text must render in the upper background, above the video slot.
        self.assertTrue(any(min(pixels[n:n+3])>140 for n in range(0,100*45*3,3)))
        gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData('source'))
        gui.persist_current_timeline_project_data()
        source_out=self.host.root/'layout_source.mp4'
        with contextlib.redirect_stdout(io.StringIO()):
            ExportWorkflow(str(self.host.root)).run(video_path=gui.video_path_edit.text(),output_path=str(source_out),
                mode='subtitle',srt_path=str(srt),subtitle_style=gui.get_subtitle_export_style(),
                output_ratio='source',video_filter_state=gui.get_video_filter_state(),
                project_state_path=self.host.service.project_file(gui.current_project_state.project_root),
                project_temp_dir=str(self.host.root/'source_render'))
        self.assertEqual(get_video_dimensions(str(source_out)),(320,180))
        pixels=subprocess.run([bin_path('ffmpeg','ffmpeg.exe'),'-v','error','-i',str(source_out),'-frames:v','1',
            '-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True).stdout
        self.assertTrue(any(min(pixels[n:n+3])>140 for n in range(0,320*35*3,3)))
        gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData('9:16'))
        fast=self.host.root/'fast.mp4'
        with contextlib.redirect_stdout(io.StringIO()):
            mux_audio_for_preview(gui.video_path_edit.text(),gui.video_path_edit.text(),str(fast),
                target_width=100,target_height=180,video_filter_state=gui.get_video_filter_state())
        self.assertEqual(get_video_dimensions(str(fast)),(100,180))
        snapshot=capture_snapshot(gui)
        dest=self.host.root/'capcut'
        DraftExporter(ffmpeg=bin_path('ffmpeg','ffmpeg.exe'),ffprobe=bin_path('ffmpeg','ffprobe.exe')).export(snapshot,dest)
        draft=json.loads((dest/'draft_content.json').read_text(encoding='utf-8'))
        self.assertTrue(any(tr['name']=='T1 Text' for tr in draft['tracks']))
        self.assertFalse((dest/'layout_base.mp4').exists())
        self.assertEqual(draft['canvas_config']['height'],180)


if __name__=='__main__': unittest.main()
