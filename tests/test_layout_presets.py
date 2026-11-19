"""Saved layouts are portable visual recipes, with transactional editor history."""
import copy
import json
import subprocess
import unittest
from unittest.mock import patch

import test_performance_queue as harness
from app import layout_presets as recipes
from app.layers.base import LayerType
from app.layers.image import ImageLayer
from app.layers.text import TextLayer
from app.layers.background import BackgroundLayer
from app.layers.timeline import Timeline, Track
from app.layers.transform import Transform
from app.layers.keyframe import KeyframeTrack, Keyframe
from app.video_layout import config, map_point


class RecipeTests(unittest.TestCase):
    def test_portability_timing_keyframes_and_canvas_placement(self):
        layout = config(dict(template='frame', background='image', image='D:/private/background.jpg'))
        controls = dict(ratio='9:16', scale_mode='fit', focus=[.5, .5])
        logo = ImageLayer(source='D:/private/logo.jpg', start=2, end=8,
                          transform=Transform(x=15, y=-25, scale_x=.5, scale_y=.6,
                              keyframes=[KeyframeTrack('x', [Keyframe(2, 15), Keyframe(8, 30)])]),
                          metadata={'_canvas_layouts': {'old': 'D:/private/file'}})
        text = TextLayer(text='My title', font_size=64, start=0, end=10)
        model = Timeline(duration=10, tracks=[Track(name='L1 Logo', type=LayerType.IMAGE, layers=[logo]),
            Track(name='T1 Text', type=LayerType.TEXT, layers=[text]),
            Track(name='Background', type=LayerType.BACKGROUND,
                  layers=[BackgroundLayer(mode='image', source='D:/private/background.jpg', end=10)])])
        styles = {'primary': {'size': 60, 'color': '#FF0000'}, 'secondary': {'size': 45}}
        recipe = recipes.capture(model, layout=layout, controls=controls, styles=styles,
                                 canvas=(1080, 1920), source=(1920, 1080))
        self.assertNotIn('private', json.dumps(recipe))
        original = copy.deepcopy(recipe)
        tracks, result_styles = recipes.materialize(recipe, layout=layout, controls=controls,
            canvas=(540, 960), source=(720, 1280), duration=20)
        self.assertEqual(recipe, original)
        result = tracks[0]['track']['layers'][0]
        self.assertEqual((result['start'], result['end']), (4, 16))
        self.assertNotEqual(result['id'], logo.id)
        self.assertEqual(result['source'], recipes.sample_asset(recipes.LOGO_ASSET))
        before = map_point(.15, -.25, layout, (1920, 1080), (1080, 1920))
        after = map_point(result['transform']['x']/100, result['transform']['y']/100,
                          layout, (720, 1280), (540, 960))
        for a, b in zip(before, after):
            self.assertAlmostEqual(a, b)
        channel = result['transform']['keyframes'][0]['keyframes']
        self.assertEqual([f['time'] for f in channel], [4, 16])
        self.assertEqual(tracks[1]['track']['layers'][0]['font_size'], 32)
        self.assertEqual(result_styles['primary']['size'], 30)
        self.assertEqual(result_styles['secondary']['size'], 22)
        self.assertEqual(tracks[2]['track']['layers'][0]['end'], 20)


class LayoutPresetEditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PerformanceQueueTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        harness.PerformanceQueueTests.tearDownClass()

    def setUp(self):
        self.host = harness.PerformanceQueueTests()
        self.host.setUp()
        self.gui = self.host.editor(self.host.source())
        self.gui.current_segments = [dict(start=0, end=.8, text='New video subtitle')]
        self.gui.apply_segments_to_timeline()
        self.layout = self.gui.video_layout_controller
        self.saved = self.layout.saved
        self.saved.library.settings = self.gui.settings

    def tearDown(self):
        self.host.tearDown()

    def test_video_inspector_frame_and_manual_background_round_trip(self):
        from app.video_layout import geometry

        self.gui.add_background_btn.click()
        background = self.gui.background_controller
        background.mode.setCurrentIndex(background.mode.findData('image'))
        image = str(harness.ROOT / 'assets/capcap.png')
        background.image.setText(image)
        background.commit()
        self.assertIsNone(self.layout.applied)
        model = self.gui.timeline._timeline
        video = next(l for l in model.all_layers() if l.type == LayerType.VIDEO)
        self.gui.timeline.select_layer(video.id)
        self.gui.on_timeline_layer_selected(video.id)
        for key, value in dict(inset=8, video_height=70, video_x=40, video_y=45, corner=9, shadow=6).items():
            self.layout.frame_spins[key].setValue(value)
        self.layout.frame_apply_button.click()
        cfg = self.gui.get_video_filter_state()['video_layout']
        self.assertEqual(cfg['image'], image)
        self.assertTrue(cfg['custom_frame'])
        self.assertAlmostEqual(geometry(cfg, 320, 180, 320, 180)[0][3], 126)
        self.gui.undo_last_timeline_timing_edit()
        self.assertFalse(self.layout.current()['custom_frame'])
        self.gui.redo_last_timeline_timing_edit()
        self.assertTrue(self.layout.current()['custom_frame'])

        recipe = self.saved.capture()
        self.assertNotIn(image, json.dumps(recipe))
        target = self.host.editor(self.host.source('new-project.mp4'))
        layout = target.video_layout_controller
        layout.saved.select(recipe)
        layout.apply()
        cfg = target.get_video_filter_state()['video_layout']
        self.assertEqual(cfg['image'], recipes.sample_asset(recipes.BACKGROUND_ASSET))
        self.assertEqual(cfg['video_height'], 70)
        self.assertAlmostEqual(geometry(cfg, 320, 180, 320, 180)[0][3], 126)
        target.load_project_context(target.current_project_state)
        self.assertEqual(layout.current()['background'], 'image')
        self.assertEqual(layout.current()['inset'], 8)

    def prepare_recipe(self):
        self.layout.templates.setCurrentRow(3)
        self.layout.apply()
        background = self.select_background_inspector('image')
        background.image.setText(str(harness.ROOT/'assets/capcap.png'))
        background.apply_btn.click()
        self.gui.timeline._timeline.tracks.append(Track(name='L1 Logo', type=LayerType.IMAGE,
            layers=[ImageLayer(source=str(harness.ROOT/'assets/capcap.png'), end=1,
                               transform=Transform(x=11, y=12, scale_x=.4, scale_y=.4))]))
        self.gui.timeline._timeline.tracks.append(Track(name='T1 Text', type=LayerType.TEXT,
            layers=[TextLayer(text='Saved title', font_size=32, end=1)]))
        return self.saved.capture()

    def test_select_is_draft_apply_is_repeatable_undo_redo_and_reopen(self):
        recipe = self.prepare_recipe()
        model = self.gui.timeline._timeline
        self.layout.reset()
        model.tracks.append(Track(name='T1 Text', type=LayerType.TEXT,
                                 layers=[TextLayer(text='Existing title', end=1)]))
        before = self.saved.snapshot()
        nonvisual = [copy.deepcopy(tr.to_dict()) for tr in model.tracks if tr.type.value not in recipes.VISUAL_TYPES]
        captions = copy.deepcopy(self.gui.get_active_segments())
        self.saved.select(recipe)
        self.assertEqual(self.saved.snapshot(), before)
        self.layout.apply()
        after = self.saved.snapshot()
        self.assertNotEqual(before, after)
        self.assertEqual(self.gui.get_active_segments(), captions)
        self.assertEqual([tr.to_dict() for tr in model.tracks if tr.type.value not in recipes.VISUAL_TYPES], nonvisual)
        self.assertTrue(all(l.source == recipes.sample_asset(recipes.LOGO_ASSET)
                            for tr in model.tracks for l in tr.layers if l.type.value == 'image'))
        self.assertEqual(self.gui.get_output_ratio_key(), '16:9')
        self.assertEqual(self.gui.current_project_state.settings['package_output_controls']['ratio'], '16:9')
        self.gui.undo_last_timeline_timing_edit()
        self.assertEqual(self.saved.snapshot(), before)
        self.gui.redo_last_timeline_timing_edit()
        self.assertEqual(self.saved.snapshot(), after)
        count = sum(len(tr.layers) for tr in model.tracks)
        self.saved.select(recipe)
        self.layout.apply()
        self.assertEqual(sum(len(tr.layers) for tr in model.tracks), count)
        state = self.gui.current_project_state
        self.gui.load_project_context(state)
        self.assertIsNone(self.saved.pending)
        self.assertEqual(self.gui.get_output_ratio_key(), '16:9')
        self.assertEqual(len(recipes.visual_tracks(self.gui.timeline._timeline)), len(after['tracks']))

    def test_named_library_capture_uses_live_layout_and_survives_reopening(self):
        recipe = self.prepare_recipe()
        with patch('widgets.named_presets.QInputDialog.getText', return_value=('My layout', True)):
            self.saved.library.save_current()
        stored = self.saved.library.read()['My layout']
        self.assertEqual(stored, recipe)
        self.assertNotIn('New video subtitle', json.dumps(stored))
        # Selection must not contain the old video's translated text/audio.
        self.saved.clear_draft()
        self.saved.library.combo.setCurrentIndex(self.saved.library.combo.findData('My layout'))
        self.saved.library.load_selected()
        self.assertEqual(self.saved.pending, recipe)
        self.assertEqual(self.layout.templates.currentRow(), -1)
        before = self.saved.snapshot()
        self.layout.templates.setCurrentRow(3)
        self.assertIsNone(self.saved.pending)
        self.assertEqual(self.saved.snapshot(), before)

    def test_locked_and_playing_do_not_apply(self):
        recipe = self.prepare_recipe()
        self.saved.select(recipe)
        before = self.saved.snapshot()
        with patch.object(self.gui, '_preview_is_playing', return_value=True):
            self.layout.apply()
        self.assertEqual(self.saved.snapshot(), before)
        tr = next(tr for tr in self.gui.timeline._timeline.tracks if tr.type.value == 'image')
        tr.locked = True
        with patch('ui.controllers.layout_presets_controller.QMessageBox.warning') as warning:
            self.layout.apply()
            warning.assert_called_once()
        self.assertEqual(tr.layers[0].source, str(harness.ROOT/'assets/capcap.png'))

    def test_apply_to_vertical_video_and_export_uses_new_source_and_sample_assets(self):
        from app.runtime_paths import bin_path, subprocess_hidden_kwargs
        from app.workflows.export_workflow import ExportWorkflow
        recipe = self.prepare_recipe()
        source = self.host.root/'vertical.mp4'
        subprocess.run([bin_path('ffmpeg', 'ffmpeg.exe'), '-v', 'error', '-y', '-f', 'lavfi', '-i',
                        'color=c=red:s=180x320:d=2', '-c:v', 'libx264', str(source)],
                       check=True, capture_output=True, **subprocess_hidden_kwargs())
        target = self.host.editor(source)
        saved = target.video_layout_controller.saved
        saved.select(recipe)
        target.video_layout_controller.apply()
        self.assertEqual(target.get_output_ratio_key(), '16:9')
        self.assertEqual(target.video_layout_controller.canvas_size(), (180, 100))
        layers = target.timeline._timeline.all_layers()
        logo = next(l for l in layers if l.type.value == 'image')
        self.assertEqual(logo.end, 2.)
        before_logo = recipe['tracks'][1]['track']['layers'][0]['transform']
        before = map_point(before_logo['x']/100, before_logo['y']/100, recipe['layout'],
                           recipe['source'], recipe['canvas'])
        after = map_point(logo.transform.x/100, logo.transform.y/100, target.video_layout_controller.applied,
                          (180, 320), (180, 100))
        for a, b in zip(before, after):
            self.assertAlmostEqual(a, b)
        bg = next(l for l in layers if l.type.value == 'background')
        self.assertEqual(bg.source, recipes.sample_asset(recipes.BACKGROUND_ASSET))
        out = self.host.root/'saved-layout.mp4'
        srt = self.host.root/'new-video.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:01,800\nNew video\n', encoding='utf-8')
        ExportWorkflow(str(self.host.root)).run(video_path=str(source), output_path=str(out), mode='subtitle',
            srt_path=str(srt), subtitle_style=target.get_subtitle_export_style(),
            project_state_path=str(self.host.service.project_file(target.current_project_state.project_root)),
            output_ratio=target.get_output_ratio_key(), video_filter_state=target.get_video_filter_state(),
            project_temp_dir=str(self.host.root/'render'))
        self.assertTrue(out.is_file())
        probe = subprocess.run([bin_path('ffmpeg', 'ffprobe.exe'), '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,duration', '-of', 'json', str(out)],
            check=True, capture_output=True, **subprocess_hidden_kwargs())
        stream = json.loads(probe.stdout)['streams'][0]
        self.assertEqual((stream['width'], stream['height']), (180, 100))
        self.assertAlmostEqual(float(stream['duration']), 2., places=1)

    def test_invalid_recipe_does_not_change_editor(self):
        before = self.saved.snapshot()
        recipe = dict(version=1, duration=0)
        with self.assertRaises(ValueError):
            self.saved.select(recipe)
        self.assertEqual(self.saved.snapshot(), before)

    def test_preview_shows_saved_layer_types_and_visibility_before_applying(self):
        from app.layers.blur import BlurLayer
        from app.layers.mask import MaskLayer
        from app.layers.text import TextLayer

        self.prepare_recipe()
        model = self.gui.timeline._timeline
        model.tracks.extend([
            Track(name='M1', type=LayerType.MASK, layers=[MaskLayer(mode='image', end=1)]),
            Track(name='B1', type=LayerType.BLUR, layers=[BlurLayer(width=.4, height=.2, end=1, visible=False)]),
            Track(name='T1 Text', type=LayerType.TEXT, layers=[
                TextLayer(text='First line', end=1), TextLayer(text='Second line', end=1),
            ]),
        ])
        saved = self.saved.capture()
        self.layout.templates.setCurrentRow(1)
        preview = self.layout.diagram
        translate = harness.main_window.t
        self.assertIn(translate('Subtitle position'), preview.inventory.text())
        self.assertNotIn(translate('Logo'), preview.inventory.text())
        before = model.to_dict()

        self.saved.select(saved)
        legend = preview.inventory.text()
        for category in ('Background image', 'Logo', 'Mask overlay image'):
            self.assertIn(translate(category), legend)
        self.assertIn(f"{translate('Text Layer')} ×3", legend)
        self.assertIn(translate('{layer} (hidden)', layer=translate('Blur Area')), legend)
        preview.canvas.resize(330, 224)
        self.assertFalse(preview.canvas.grab().isNull())
        self.assertEqual(model.to_dict(), before)

    def test_saved_blur_survives_preview_refresh_in_quality_mode(self):
        from app.layers.blur import BlurLayer

        self.layout.templates.setCurrentRow(3)
        self.layout.apply()
        background = self.select_background_inspector('blur')
        background.blur.setValue(36)
        background.apply_btn.click()
        model = self.gui.timeline._timeline
        blur = BlurLayer(start=0, end=.7, position_x=.2, position_y=.3,
                         width=.4, height=.2, blur_strength=32, blur_opacity=.65,
                         pixelate=True, pixelate_size=18)
        model.tracks.append(Track(name='B1', type=LayerType.BLUR, layers=[blur]))
        self.gui.blur_area_btn.setChecked(True)
        recipe = self.saved.capture()
        self.saved.select(recipe)
        # Use Quality's signal handler with the offscreen editor to exercise
        # its overlay-to-timeline synchronization during preset application.
        quality_handler = harness.main_window.VideoTranslatorGUI.on_preview_blur_region_changed
        self.gui.video_view.blurRegionChanged.connect(lambda: quality_handler(self.gui))
        self.layout.apply()
        restored = next(l for l in model.all_layers() if l.type == LayerType.BLUR)
        self.assertTrue(restored.metadata.get('layout_role'))
        for key in ('start', 'end', 'position_x', 'position_y', 'width', 'height',
                    'blur_strength', 'blur_opacity', 'pixelate', 'pixelate_size'):
            self.assertEqual(getattr(restored, key), getattr(blur, key), key)
        self.assertTrue(self.gui._blur_effect_enabled())
        self.assertEqual(self.layout.current()['background'], 'blur')
        self.assertEqual(self.layout.current()['blur'], 36)
        self.layout.templates.setCurrentRow(0)
        self.layout.apply()
        self.assertFalse(any(l.type == LayerType.BLUR for l in model.all_layers()))

    def select_background_inspector(self, mode):
        self.gui.timeline.select_layer('layout-background')
        self.gui.on_timeline_layer_selected('layout-background')
        background = self.gui.background_controller
        background.mode.setCurrentIndex(background.mode.findData(mode))
        return background

    def test_free_layout_clears_removed_preset_logo_and_undo_redo_refreshes_preview(self):
        recipe = self.prepare_recipe()
        self.saved.select(recipe)
        self.layout.apply()
        self.assertTrue(self.gui.video_view.logo_overlay._regions)
        self.layout.templates.setCurrentRow(0)
        self.layout.apply()
        self.assertFalse(any(l.type.value == 'image' for tr in self.gui.timeline._timeline.tracks for l in tr.layers))
        self.assertEqual(self.gui.video_view.logo_overlay._regions, [])
        self.assertIsNone(self.gui._logo_overlay_layer)
        self.gui.undo_last_timeline_timing_edit()
        self.assertTrue(self.gui.video_view.logo_overlay._regions)
        self.gui.redo_last_timeline_timing_edit()
        self.assertEqual(self.gui.video_view.logo_overlay._regions, [])

    def test_apply_clears_stale_effect_fallbacks_and_undo_restores_them(self):
        recipe = self.prepare_recipe()
        state = self.gui.current_project_state
        old = {'enabled': True, 'regions': [dict(x=.2, y=.3, width=.4, height=.2)]}
        state.set_setting('mask_state', copy.deepcopy(old))
        state.set_setting('blur_state', copy.deepcopy(old))
        self.saved.select(recipe)
        self.layout.apply()
        self.assertEqual(self.gui.current_project_state.settings['mask_state']['regions'], [])
        self.assertEqual(self.gui.current_project_state.settings['blur_state']['regions'], [])
        self.gui.undo_last_timeline_timing_edit()
        self.assertEqual(self.gui.current_project_state.settings['mask_state'], old)
        self.assertEqual(self.gui.current_project_state.settings['blur_state'], old)


if __name__ == '__main__':
    unittest.main()
