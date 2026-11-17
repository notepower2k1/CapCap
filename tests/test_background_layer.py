"""Independent backgrounds do not require or modify template/subtitle layout."""
import copy
import contextlib
import io
import json
from pathlib import Path
import subprocess
import unittest
import test_performance_queue as harness
from app.video_layout import geometry
from runtime_paths import bin_path


class BackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):harness.PerformanceQueueTests.setUpClass()
    @classmethod
    def tearDownClass(cls):harness.PerformanceQueueTests.tearDownClass()
    def setUp(self):self.host=harness.PerformanceQueueTests();self.host.setUp()
    def tearDown(self):self.host.tearDown()

    def editor(self):
        gui=self.host.editor(self.host.source())
        gui.current_segments=[dict(start=0,end=.8,text='Hello')];gui.apply_segments_to_timeline()
        return gui

    def test_background_button_is_available_before_transcription(self):
        gui=self.host.editor(self.host.source())
        self.assertFalse(gui.current_segments)
        self.assertTrue(gui.add_background_btn.isEnabled())
        gui.add_background_btn.click()
        self.assertEqual(gui.video_layout_controller.current()['template'],'free')
        self.assertIsNone(gui.video_layout_controller.applied)

    def test_preview_button_adds_background_with_own_inspector_without_applying_template(self):
        gui=self.editor()
        before=copy.deepcopy(gui.subtitle_presentation_controller.cfg['styles'])
        self.assertTrue(gui.add_background_btn.isEnabled())
        gui.add_background_btn.click()
        ctrl=gui.background_controller
        self.assertIs(gui.inspector_stack.currentWidget(),ctrl.scroll)
        self.assertIsNone(gui.video_layout_controller.applied)
        self.assertEqual(gui.subtitle_presentation_controller.cfg['styles'],before)
        cfg=gui.video_layout_controller.current()
        self.assertEqual(cfg['template'],'free')
        slot,displayed=geometry(cfg,320,180,320,180)
        self.assertEqual(slot,(0,0,320,180))
        self.assertEqual(displayed,(0,0,320,180))
        ctrl.mode.setCurrentIndex(ctrl.mode.findData('image'))
        ctrl.image.setText(str(harness.ROOT/'assets/capcap.png'));ctrl.commit()
        self.assertEqual(gui.get_video_filter_state()['video_layout']['background'],'image')
        ctrl.visible.setChecked(False);ctrl.commit()
        self.assertIsNone(gui.video_layout_controller.current())
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(gui.video_layout_controller.current()['background'],'image')
        ctrl.delete();self.assertIsNone(gui.video_layout_controller.current())
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(gui.video_layout_controller.current()['background'],'image')
        state=gui.current_project_state
        gui.load_project_context(state)
        self.assertEqual(gui.video_layout_controller.current()['background'],'image')
        self.assertIsNone(gui.video_layout_controller.applied)

    def test_manual_background_survives_template_reset(self):
        gui=self.editor();gui.on_add_timeline_layer('background')
        ctrl=gui.background_controller
        ctrl.color.setText('#FF0000');ctrl.commit()
        manual=ctrl.layer_id
        layout=gui.video_layout_controller;layout.templates.setCurrentRow(3);layout.apply();layout.reset()
        self.assertIsNone(layout.applied)
        self.assertEqual(layout.current()['color'],'#FF0000')
        self.assertIn(manual,[l.id for tr in gui.timeline._timeline.tracks for l in tr.layers])

    def test_independent_background_export_and_capcut(self):
        from app.workflows.export_workflow import ExportWorkflow
        from app.capcut.draft_export import DraftExporter
        from ui.controllers.capcut_export_controller import capture_snapshot
        gui=self.editor();gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData('9:16'))
        gui.on_add_timeline_layer('background');ctrl=gui.background_controller
        ctrl.color.setText('#FF0000');ctrl.commit()
        srt=self.host.root/'cue.srt';srt.write_text('1\n00:00:00,000 --> 00:00:00,800\nHello\n',encoding='utf-8')
        out=self.host.root/'background.mp4'
        with contextlib.redirect_stdout(io.StringIO()):
            ExportWorkflow(str(self.host.root)).run(video_path=gui.video_path_edit.text(),output_path=str(out),mode='subtitle',
                srt_path=str(srt),output_ratio='9:16',video_filter_state=gui.get_video_filter_state(),
                project_temp_dir=str(self.host.root/'render'),subtitle_style=gui.get_subtitle_export_style())
        pixels=subprocess.run([bin_path('ffmpeg','ffmpeg.exe'),'-v','error','-i',str(out),'-frames:v','1',
                               '-pix_fmt','rgb24','-f','rawvideo','-'],capture_output=True,check=True).stdout
        self.assertGreater(pixels[0],220);self.assertLess(pixels[2],50)
        center=(90*100+50)*3
        self.assertGreater(pixels[center+2],220);self.assertLess(pixels[center],50)
        snapshot=capture_snapshot(gui)
        self.assertEqual(snapshot['video_layout']['template'],'free')
        destination=self.host.root/'capcut'
        DraftExporter(ffmpeg=bin_path('ffmpeg','ffmpeg.exe'),ffprobe=bin_path('ffmpeg','ffprobe.exe')).export(snapshot,destination)
        draft=json.loads((destination/'draft_content.json').read_text(encoding='utf-8'))
        self.assertEqual(draft['canvas_config']['height'],180)


if __name__=='__main__':unittest.main()
