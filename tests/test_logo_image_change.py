"""Changing a logo asset preserves edits and uses the selected timeline layer."""
import copy
import unittest
from unittest.mock import patch

import test_performance_queue as harness
from app.layers.base import LayerType
from app.layers.image import ImageLayer
from app.layers.sync_bridge import find_or_create_track
from app.layers.transform import Transform


class LogoImageTests(unittest.TestCase):
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
        self.track = find_or_create_track(self.gui.timeline._timeline, 'L1 Logo', LayerType.IMAGE, 80)
        self.original = str(harness.ROOT / 'assets/capcap.png')
        self.replacement = str(harness.ROOT / 'assets/capcap_white.png')
        self.other = ImageLayer(source=self.original, start=0, end=.8)
        self.layer = ImageLayer(source=self.original, start=.1, end=.7, opacity=.6,
                               transform=Transform(x=12, y=9, scale_x=.7, scale_y=.8, rotation=17))
        self.track.layers.extend([self.other, self.layer])
        self.gui.timeline._selected_layer_id = self.layer.id
        self.gui._show_logo_inspector_for_track(self.track, self.layer)

    def tearDown(self):
        self.host.tearDown()

    def test_change_preserves_properties_and_can_undo_redo_and_reload(self):
        before = copy.deepcopy(self.layer.to_dict())
        with patch.object(harness.main_window.QFileDialog, 'getOpenFileName', return_value=(self.replacement, '')):
            self.gui.logo_inspector_change_image_btn.click()
        expected = dict(before, source=self.replacement)
        self.assertEqual(self.layer.to_dict(), expected)
        self.assertEqual(self.other.source, self.original)
        self.assertEqual(self.gui.logo_inspector_image_edit.toolTip(), self.replacement)
        self.gui.undo_last_timeline_timing_edit()
        self.assertEqual(self.layer.to_dict(), before)
        self.gui.redo_last_timeline_timing_edit()
        self.assertEqual(self.layer.to_dict(), expected)
        state = self.gui.current_project_state
        self.gui.load_project_context(state)
        restored = next(l for tr in self.gui.timeline._timeline.tracks for l in tr.layers if l.id == self.layer.id)
        self.assertEqual(restored.to_dict(), expected)

    def test_cancel_invalid_locked_and_playing_do_not_change_source(self):
        with patch.object(harness.main_window.QFileDialog, 'getOpenFileName', return_value=('', '')):
            self.gui.change_logo_image()
        invalid = str(self.host.root / 'invalid.png')
        with patch.object(harness.main_window.QFileDialog, 'getOpenFileName', return_value=(invalid, '')), \
                patch.object(harness.main_window.QMessageBox, 'warning') as warning:
            self.gui.change_logo_image()
            warning.assert_called_once()
        with patch.object(harness.main_window.QFileDialog, 'getOpenFileName') as picker:
            self.layer.locked = True
            self.gui.change_logo_image()
            self.layer.locked = False
            self.track.locked = True
            self.gui.change_logo_image()
            self.track.locked = False
            with patch.object(self.gui, '_preview_is_playing', return_value=True):
                self.gui.change_logo_image()
            picker.assert_not_called()
        self.assertEqual(self.layer.source, self.original)

    def test_timed_preview_clears_logo_when_its_entire_track_is_removed(self):
        for mode, refresh in (
            ('Performance', self.gui.refresh_timed_layer_preview),
            ('Quality', lambda: harness.main_window.VideoTranslatorGUI.refresh_timed_layer_preview(self.gui)),
        ):
            with self.subTest(mode=mode):
                if self.track not in self.gui.timeline._timeline.tracks:
                    self.gui.timeline._timeline.tracks.append(self.track)
                self.gui._timed_layer_preview_signature = None
                refresh()
                self.assertTrue(self.gui.video_view.logo_overlay._regions)
                self.gui.timeline._timeline.tracks.remove(self.track)
                refresh()
                self.assertEqual(self.gui.video_view.logo_overlay._regions, [])
                self.assertIsNone(self.gui._logo_overlay_layer)


if __name__ == '__main__':
    unittest.main()
