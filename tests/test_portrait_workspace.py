import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
for folder in (PROJECT_ROOT, PROJECT_ROOT / "app", PROJECT_ROOT / "ui"):
    sys.path.insert(0, str(folder))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer, QEvent, QObject
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from ui.main_window import VideoTranslatorGUI
from ui.i18n import t

app = QApplication.instance() or QApplication([])


class TestPortraitWorkspace(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("ui.main_window.QTimer.singleShot"))
        self.enterContext(patch("ui.main_window.apply_windows_dark_title_bar_impl"))
        self.gui = VideoTranslatorGUI()
        self.gui._initial_layout_finalized = True
        self.gui.setWindowState(Qt.WindowNoState)
        self.gui.resize(1920, 1080)
        self.gui.video_view.set_video_dimensions(1080, 1920)
        self.gui.set_workspace_layout_mode("portrait")
        self.gui.show()
        app.processEvents()

    def tearDown(self):
        self.gui.hide()
        self.gui._terminate_workers()
        self.gui.deleteLater()

    def test_inspector_cards_fill_width_after_responsive_layout_changes(self):
        for width, height in ((1920, 1080), (1600, 900)):
            self.gui.resize(width, height)
            self.gui.apply_responsive_layout(width, height)
            for index in (0, 3):
                with self.subTest(width=width, inspector=index):
                    self.gui.inspector_stack.setCurrentIndex(index)
                    app.processEvents()
                    scroll = self.gui.inspector_stack.currentWidget()
                    self.assertGreaterEqual(scroll.widget().width(), scroll.viewport().width())

    def test_layer_toolbar_fills_only_inspector_height_while_buttons_stay_compact(self):
        self.gui.apply_responsive_layout(1920, 1080)
        app.processEvents()
        toolbar = self.gui.vertical_layer_toolbar
        self.assertIs(toolbar.parentWidget(), self.gui.portrait_top_deck)
        self.assertEqual(toolbar.height(), self.gui.portrait_top_deck.height())
        toolbar_bottom = toolbar.mapTo(self.gui.preview_timeline_splitter, toolbar.rect().bottomLeft()).y()
        timeline_top = self.gui.timeline_card.mapTo(
            self.gui.preview_timeline_splitter, self.gui.timeline_card.rect().topLeft()
        ).y()
        self.assertLess(toolbar_bottom, timeline_top)
        self.assertEqual(self.gui.vertical_text_btn.height(), 30)
        self.assertEqual(self.gui.vertical_ocr_btn.height(), 30)

    def test_landscape_restores_inspector_width_limits(self):
        self.gui.set_workspace_layout_mode("landscape")
        self.gui.apply_responsive_layout(1920, 1080)
        app.processEvents()
        self.assertEqual(self.gui.video_inspector_card.maximumWidth(), 560)
        self.assertTrue(self.gui.vertical_layer_toolbar.isHidden())

    def test_returning_to_landscape_does_not_show_detached_preview_window(self):
        shown_windows = []

        class Observer(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow():
                    shown_windows.append(obj.objectName() or type(obj).__name__)
                return False

        observer = Observer()
        app.installEventFilter(observer)
        try:
            self.gui.show_loading_overlay()
            self.gui.set_workspace_layout_mode("landscape")
            app.processEvents()
            self.assertEqual(shown_windows, [])
        finally:
            app.removeEventFilter(observer)

    def test_portrait_inspector_defaults_to_400_pixels_and_remains_resizable(self):
        self.gui.apply_responsive_layout(1920, 1080)
        self.gui._set_default_preview_timeline_sizes()
        app.processEvents()
        splitter = self.gui.preview_timeline_splitter
        self.assertAlmostEqual(splitter.sizes()[0], 400, delta=2)
        available = sum(splitter.sizes())
        splitter.setSizes([280, available - 280])
        app.processEvents()
        self.assertAlmostEqual(splitter.sizes()[0], 280, delta=2)
        self.assertGreaterEqual(splitter.sizes()[1], self.gui._responsive_timeline_minimum_height)

    def test_layout_switch_is_a_text_action_in_more_menu(self):
        action = self.gui.workspace_layout_toggle_action
        self.assertIn(action, self.gui.more_actions_btn.menu().actions())
        self.assertTrue(action.icon().isNull())
        self.assertEqual(action.text(), t("Switch to landscape layout (16:9)"))
        self.gui.set_workspace_layout_mode("landscape")
        self.assertEqual(action.text(), t("Switch to portrait layout (9:16)"))

    def test_auto_fit_button_keeps_the_default_breathing_buffer(self):
        self.gui.current_segments = [{"start": 0.0, "end": 1.0, "_audio_end": 2.0}]
        self.gui.current_translated_segments = []
        self.gui.batch_fit_voice_btn.setEnabled(True)
        with patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.No) as confirm:
            self.gui.batch_fit_voice_btn.click()
        confirm.assert_called_once()
        self.assertIn("0.15", confirm.call_args.args[2])

    def test_default_inspector_height_leaves_timeline_space_on_short_windows(self):
        self.gui.resize(1920, 720)
        self.gui.apply_responsive_layout(1920, 720)
        app.processEvents()
        self.gui._set_default_preview_timeline_sizes()
        app.processEvents()
        inspector_height, timeline_height = self.gui.preview_timeline_splitter.sizes()
        self.assertLessEqual(inspector_height, 400)
        self.assertGreaterEqual(timeline_height, self.gui._responsive_timeline_minimum_height)

    def test_layout_switch_keeps_video_hidden_until_overlay_fade_finishes(self):
        player = MagicMock()
        player.is_playing.return_value = True
        self.gui.media_player = player
        self.gui.sync_live_subtitle_preview = MagicMock()
        self.gui.refresh_timed_layer_preview = MagicMock()
        callbacks = []
        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda ms, cb: callbacks.append((ms, cb))):
            self.gui.workspace_layout_toggle_action.trigger()
            self.assertTrue(self.gui._loading_overlay.isVisible())
            self.assertTrue(self.gui._project_loading_in_progress)
            self.assertFalse(self.gui.video_view.video_surface.isVisible())
            self.assertFalse(self.gui.workspace_layout_toggle_action.isEnabled())
            next(cb for ms, cb in callbacks if ms == 50)()
            self.assertEqual(self.gui._workspace_layout_mode, "landscape")
            self.assertTrue(self.gui._project_loading_in_progress)
            self.assertFalse(self.gui.video_view.video_surface.isVisible())
            self.gui._loading_overlay._anim.stop()
            self.gui._loading_overlay._on_fade_finished()
            self.assertTrue(self.gui._project_loading_in_progress)
            self.assertFalse(self.gui._loading_overlay.isVisible())
            self.assertFalse(self.gui.video_view.video_surface.isVisible())
            self.assertIsNone(self.gui._loading_overlay.graphicsEffect())
            self.gui.video_view.video_surface.setGeometry(3, 4, 111, 222)
            next(cb for ms, cb in callbacks if ms == 100)()
            self.assertFalse(self.gui._project_loading_in_progress)
            self.assertTrue(self.gui.video_view.video_surface.isVisible())
            self.assertEqual(self.gui.video_view.video_surface.geometry(), self.gui.video_view._video_surface_rect())
            player.set_preview_framing.assert_called_once()
            self.assertTrue(self.gui.workspace_layout_toggle_action.isEnabled())
            player.pause.assert_called_once()
            player.play.assert_called_once()
            self.gui.sync_live_subtitle_preview.assert_called_once()

            callbacks.clear()
            self.gui.workspace_layout_toggle_action.trigger()
            next(cb for ms, cb in callbacks if ms == 50)()
            self.gui._loading_overlay._anim.stop()
            self.gui._loading_overlay._on_fade_finished()
            next(cb for ms, cb in callbacks if ms == 100)()
            self.assertEqual(self.gui._workspace_layout_mode, "portrait")
            self.assertTrue(self.gui.video_view.video_surface.isVisible())

    def test_layout_switch_preserves_active_freeze_timing(self):
        player = MagicMock()
        player.is_playing.return_value = True
        self.gui.media_player = player
        self.gui.sync_live_subtitle_preview = MagicMock()
        self.gui.refresh_timed_layer_preview = MagicMock()
        timer = QTimer(self.gui)
        timer.start(20)
        self.gui._freeze_playback_timer = timer
        self.gui._active_freeze_warp = {"id": "freeze"}
        self.gui._freeze_start_mono = 95.0
        self.gui._freeze_anchor_ms = 2000
        callbacks = []
        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda ms, cb: callbacks.append((ms, cb))), \
                patch("ui.main_window.time.monotonic", return_value=100.0) as clock:
            self.gui.workspace_layout_toggle_action.trigger()
            self.assertFalse(timer.isActive())
            next(cb for ms, cb in callbacks if ms == 50)()
            clock.return_value = 101.0
            self.gui._loading_overlay._anim.stop()
            self.gui._loading_overlay._on_fade_finished()
            next(cb for ms, cb in callbacks if ms == 100)()
            self.assertTrue(timer.isActive())
            self.assertEqual(self.gui._freeze_start_mono, 96.0)
            self.assertEqual(self.gui._active_freeze_warp, {"id": "freeze"})
            player.freeze_video_frame.assert_called_once_with(2000)
        timer.stop()

    def test_preview_show_resynchronizes_stale_native_surface_bounds(self):
        view = self.gui.video_view
        view.video_surface.setGeometry(3, 4, 111, 222)
        view.showEvent(QShowEvent())
        self.assertEqual(view.video_surface.geometry(), view._video_surface_rect())

    def test_deferred_preview_restore_does_not_reveal_a_new_project_while_loading(self):
        callbacks = []
        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda ms, cb: callbacks.append((ms, cb))):
            self.gui.workspace_layout_toggle_action.trigger()
            next(cb for ms, cb in callbacks if ms == 50)()
            self.gui._loading_overlay._anim.stop()
            self.gui._loading_overlay._on_fade_finished()
            self.gui._current_video_path = "another.mp4"
            self.gui.show_loading_overlay("another.mp4")
            next(cb for ms, cb in callbacks if ms == 100)()
            self.assertTrue(self.gui._project_loading_in_progress)
            self.assertTrue(self.gui._loading_overlay.isVisible())
            self.assertFalse(self.gui.video_view.video_surface.isVisible())

    def test_video_inspector_stays_selected_when_backend_is_not_ready(self):
        track = SimpleNamespace(name="V1 Video")
        self.gui.inspector_stack.setCurrentIndex(0)
        with patch.object(self.gui, "_video_filter_inspector_available", return_value=False):
            self.gui._show_video_inspector_for_track(track)
        self.assertEqual(self.gui.inspector_stack.currentIndex(), 3)
        self.assertFalse(self.gui.video_inspector_card.isEnabled())
        with patch.object(self.gui, "_video_filter_inspector_available", return_value=True):
            self.gui._show_video_inspector_for_track(track)
        self.assertEqual(self.gui.inspector_stack.currentIndex(), 3)
        self.assertTrue(self.gui.video_inspector_card.isEnabled())


class TestDefaultVideoInspector(unittest.TestCase):
    def test_loading_video_selects_video_inspector_after_media_is_ready(self):
        gui = MagicMock(spec=VideoTranslatorGUI)
        for name in ("video_path_edit", "transcript_text", "translated_text", "timeline", "media_player", "video_view"):
            setattr(gui, name, MagicMock())
        gui._media_backend_ready = True
        gui._project_loading_in_progress = False
        gui.isVisible.return_value = True
        layer = SimpleNamespace(id="video-layer")
        track = SimpleNamespace(name="V1 Video", layers=[layer])
        gui.timeline._timeline = SimpleNamespace(tracks=[track])
        gui._select_video_layer_for_inspector = MagicMock(
            side_effect=lambda: VideoTranslatorGUI._select_video_layer_for_inspector(gui)
        )
        gui._set_default_preview_timeline_sizes = MagicMock()
        callbacks = []
        with patch("ui.main_window.QTimer.singleShot", side_effect=lambda ms, cb: callbacks.append(cb)), \
                patch("views.launcher.LauncherWindow.add_recent"), \
                patch("views.launcher._get_video_duration", return_value=10.0):
            VideoTranslatorGUI.load_video_project(gui, "video.mp4")
            gui._set_default_preview_timeline_sizes.assert_not_called()
            for _ in range(3):
                callbacks.pop(0)()
        gui._set_default_preview_timeline_sizes.assert_called_once()
        gui.timeline.select_layer.assert_called_once_with(layer.id)
        gui.on_timeline_layer_selected.assert_called_once_with(layer.id)


if __name__ == "__main__":
    unittest.main()
