"""Cold Quality startup restores layout before a native player exists."""
import unittest
import test_performance_queue as harness
from app.video_layout import KEY, config


class VideoLayoutStartupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PerformanceQueueTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        harness.PerformanceQueueTests.tearDownClass()

    def setUp(self):
        self.host = harness.PerformanceQueueTests()
        self.host.setUp()

    def tearDown(self):
        self.host.tearDown()

    def test_new_project_opens_with_bootstrap_player(self):
        gui = self.host.editor(self.host.source(), cls=harness.main_window.VideoTranslatorGUI)
        self.assertEqual(gui.media_player.backend_name, 'bootstrap')
        self.assertIsNone(gui.video_layout_controller.applied)

    def test_saved_layout_restores_geometry_before_player_initialization(self):
        video = self.host.source()
        state = self.host.service.ensure_project(str(video))
        state.set_setting(KEY, config({'template': 'frame', 'corner': 24}))
        self.host.service.save_project(state)
        gui = self.host.editor(video, cls=harness.main_window.VideoTranslatorGUI)
        self.assertEqual(gui.media_player.backend_name, 'bootstrap')
        self.assertEqual(gui.video_view.video_layout['template'], 'frame')
        self.assertEqual(gui.video_view.video_layout['corner'], 24)

    def test_export_audio_quality_is_saved_per_project(self):
        video = self.host.source()
        gui = self.host.editor(video)
        self.assertEqual(gui.get_export_audio_quality(), "standard")
        combo = gui.export_audio_quality_combo
        combo.setCurrentIndex(combo.findData("economy"))
        reopened = self.host.editor(video)
        self.assertEqual(reopened.get_export_audio_quality(), "economy")
        other = self.host.editor(self.host.source("another.mp4"))
        self.assertEqual(other.get_export_audio_quality(), "standard")

    def test_independent_background_restores_without_a_template(self):
        from app.layers.timeline import Timeline, Track
        from app.layers.base import LayerType
        from app.layers.background import BackgroundLayer
        video=self.host.source()
        state=self.host.service.ensure_project(str(video))
        timeline=Timeline(duration=1,tracks=[Track(name='Background',type=LayerType.BACKGROUND,
            layers=[BackgroundLayer(start=0,end=1,color='#FF0000')])])
        self.host.service.save_json_artifact(state,'timeline','timeline/timeline.json',timeline.to_dict())
        gui=self.host.editor(video,cls=harness.main_window.VideoTranslatorGUI)
        self.assertIsNone(gui.video_layout_controller.applied)
        self.assertEqual(gui.video_view.video_layout['template'],'free')
        self.assertEqual(gui.video_view.video_layout['color'],'#FF0000')


if __name__ == '__main__':
    unittest.main()
