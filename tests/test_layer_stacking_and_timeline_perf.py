#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_layer_stacking_and_timeline_perf.py

Regression test suite for timeline performance optimizations:
1. Overlap layout caching during dragging (avoids O(N log N) recompute on every pixel).
2. Magnetic snap visual guide line and _snap_guide_time tracking.
3. Waveform and thumbnail strip pixmap caching and reuse across playhead updates.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root, app, and ui are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ui.views.editor.timeline import EditorTimeline
from ui.widgets.mpv_video_view import MpvVideoView
from app.layers.timeline import Track, Clip
from app.layers.base import LayerType


class TestLayerStackingAndTimelinePerf(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.timeline = EditorTimeline()
        self.timeline.set_duration_ms(30000)

    def tearDown(self):
        if hasattr(self, "timeline") and self.timeline:
            self.timeline.reset_timeline()
            self.timeline.deleteLater()
            self.timeline = None
        self.app.processEvents()

    def test_horizontal_drag_avoids_recomputing_overlap_rows(self):
        """Verify horizontal drag does NOT recompute _compute_overlap_rows on every pixel."""
        timeline = self.timeline
        segments = [
            {"start": 1.0, "end": 3.0, "text": "seg0"},
            {"start": 2.5, "end": 5.0, "text": "seg1"},
            {"start": 6.0, "end": 8.0, "text": "seg2"},
        ]
        timeline.set_segments(segments)
        timeline.set_duration_ms(30000)
        track = next(t for t in timeline._timeline.tracks if timeline._is_subtitle_track(t))

        # Initial populate of overlap cache
        pix = QPixmap(800, 300)
        p = QPainter(pix)
        timeline._draw_track_layers(p, track, 0, 30, 80)
        p.end()

        self.assertIn(track.id, timeline._overlap_layout_cache)
        cached_before = timeline._overlap_layout_cache[track.id]
        initial_sub_rows = dict(cached_before[0])

        seg0_layer = track.layers[0]
        anchor_y = 50.0
        timeline._drag_state = {
            "type": "move",
            "layer_id": seg0_layer.id,
            "track_id": str(track.id),
            "row_index": 0,
            "anchor_time": 1.0,
            "start_time": 1.0,
            "end_time": 3.0,
            "anchor_y": anchor_y,
            "initial_row": 0,
        }
        setattr(seg0_layer, "row_index", 0)

        # Patch _compute_overlap_rows to verify it is NOT called during horizontal drag
        with patch.object(timeline, "_compute_overlap_rows", wraps=timeline._compute_overlap_rows) as mock_compute:
            for step in range(1, 15):
                target_x = (1.0 + step * 0.05) * timeline.pixels_per_second + timeline.CONTENT_LEFT_PAD
                pos = QPointF(float(target_x), anchor_y)
                event = QMouseEvent(
                    QEvent.Type.MouseMove,
                    pos,
                    pos,
                    Qt.MouseButton.NoButton,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                timeline.mouseMoveEvent(event)

            # _compute_overlap_rows should NOT be called at all across the 14 horizontal move steps!
            self.assertEqual(mock_compute.call_count, 0)

        # Cache remains valid and sub-row assignment is stable
        self.assertIn(track.id, timeline._overlap_layout_cache)
        cached_during = timeline._overlap_layout_cache[track.id]
        self.assertEqual(cached_during[0], initial_sub_rows)

        # On mouse release, cache is cleared cleanly
        rel_event = QMouseEvent(
            QEvent.Type.MouseButtonRelease,
            QPointF(float(target_x), anchor_y),
            QPointF(float(target_x), anchor_y),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
        )
        timeline.mouseReleaseEvent(rel_event)
        self.assertNotIn(track.id, timeline._overlap_layout_cache)

    def test_vertical_drag_clears_cache_on_row_change(self):
        """Verify dragging to a different sub-row clears overlap cache so heights/stacking recompute."""
        timeline = self.timeline
        timeline.set_duration(30.0)
        segments = [
            {"start": 0.0, "end": 4.0, "text": "seg0"},
            {"start": 10.0, "end": 14.0, "text": "seg1"},
            {"start": 5.0, "end": 8.0, "text": "seg2", "row_index": 0},
        ]
        timeline.set_segments(segments)
        track = next(t for t in timeline._timeline.tracks if timeline._is_subtitle_track(t))

        # Initial populate
        pix = QPixmap(800, 300)
        p = QPainter(pix)
        timeline._draw_track_layers(p, track, 0, 30, 80)
        p.end()

        seg2_layer = track.layers[2]
        anchor_y = 60.0
        timeline._drag_state = {
            "type": "move",
            "layer_id": seg2_layer.id,
            "track_id": str(track.id),
            "row_index": 0,
            "anchor_time": 5.0,
            "start_time": 5.0,
            "end_time": 8.0,
            "anchor_y": anchor_y,
            "initial_row": 0,
        }
        setattr(seg2_layer, "row_index", 0)

        # Drag vertically DOWN to row 1
        target_x = 5.0 * timeline.pixels_per_second + timeline.CONTENT_LEFT_PAD
        target_pos = QPointF(float(target_x), anchor_y + float(timeline.CHILD_TRACK_H))
        event = QMouseEvent(
            QEvent.Type.MouseMove,
            target_pos,
            target_pos,
            Qt.MouseButton.NoButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        timeline.mouseMoveEvent(event)

        # When row changes, _overlap_layout_cache should be cleared
        self.assertNotIn(track.id, timeline._overlap_layout_cache)
        self.assertEqual(timeline._drag_state["row_index"], 1)

    def test_magnetic_snap_sets_snap_guide_time_on_playhead(self):
        """Verify magnetic snap triggers when near playhead and records _snap_guide_time."""
        timeline = self.timeline
        timeline.set_segments([{"start": 1.0, "end": 3.0, "text": "cue"}])
        timeline.set_duration_ms(30000)
        timeline.set_playhead(5.0)

        track = next(t for t in timeline._timeline.tracks if timeline._is_subtitle_track(t))
        clip = track.layers[0]

        # 1. Body move snapping start to playhead (target: 5.0, proposed start: 4.98 within threshold 0.05)
        new_start = timeline._clamp_layer_move(track, clip, 4.98, 6.98)
        self.assertAlmostEqual(new_start, 5.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 5.0, places=3)

        # 2. Body move snapping end to playhead (target: 5.0, proposed end: 5.02, duration: 2.0 -> start: 3.0)
        new_start = timeline._clamp_layer_move(track, clip, 3.02, 5.02)
        self.assertAlmostEqual(new_start + 2.0, 5.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 5.0, places=3)

        # 3. Far away from snap targets -> _snap_guide_time is None
        new_start = timeline._clamp_layer_move(track, clip, 8.0, 10.0)
        self.assertAlmostEqual(new_start, 8.0, places=3)
        self.assertIsNone(timeline._snap_guide_time)

        # 4. Resize left snapping to playhead
        new_left = timeline._clamp_layer_resize(track, clip, "left", 5.03)
        self.assertAlmostEqual(new_left, 5.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 5.0, places=3)

        # 5. Resize right snapping to playhead
        new_right = timeline._clamp_layer_resize(track, clip, "right", 4.97)
        self.assertAlmostEqual(new_right, 5.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 5.0, places=3)

    def test_magnetic_snap_sets_snap_guide_time_on_adjacent_cue(self):
        """Verify magnetic snap triggers when near adjacent segment boundary."""
        timeline = self.timeline
        timeline.set_segments([
            {"start": 0.0, "end": 2.0, "text": "cue0"},
            {"start": 5.0, "end": 7.0, "text": "cue1"},
            {"start": 10.0, "end": 12.0, "text": "cue2"},
        ])
        timeline.set_duration_ms(30000)
        timeline.set_playhead(20.0)  # far away

        track = next(t for t in timeline._timeline.tracks if timeline._is_subtitle_track(t))
        cue0, cue1, cue2 = track.layers[0], track.layers[1], track.layers[2]

        # Moving cue1 near cue0 end (2.0): proposed start 2.03 -> snap to 2.0
        new_start = timeline._clamp_layer_move(track, cue1, 2.03, 4.03)
        self.assertAlmostEqual(new_start, 2.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 2.0, places=3)

        # Moving cue1 near cue2 start (10.0): proposed end 9.98 (duration 2.0) -> snap to 10.0
        new_start = timeline._clamp_layer_move(track, cue1, 7.98, 9.98)
        self.assertAlmostEqual(new_start + 2.0, 10.0, places=3)
        self.assertAlmostEqual(timeline._snap_guide_time, 10.0, places=3)

    def test_waveform_pixmap_cache_and_reuse(self):
        """Verify _draw_waveform populates _waveform_pixmap_cache and reuses it across draws."""
        timeline = self.timeline
        samples = [0.1, 0.5, 0.8, 0.3, 0.9, 0.2, 0.4]
        timeline.set_waveform_data(samples, 10.0)

        self.assertIsNone(timeline._waveform_pixmap_cache)

        # 1. Render waveform onto a test canvas
        canvas = QPixmap(600, 100)
        p = QPainter(canvas)
        timeline._draw_waveform(p, 0, 10.0, 500, 60.0, 600)
        p.end()

        # Cache must now be populated
        self.assertIsNotNone(timeline._waveform_pixmap_cache)
        cache_key, cached_pix = timeline._waveform_pixmap_cache
        self.assertIsInstance(cached_pix, QPixmap)
        self.assertFalse(cached_pix.isNull())
        pix_id = id(cached_pix)

        # 2. Render again with different playhead / viewport offset
        canvas2 = QPixmap(600, 100)
        p2 = QPainter(canvas2)
        timeline._draw_waveform(p2, -50, 10.0, 500, 60.0, 600)
        p2.end()

        # Cached pixmap instance should be reused directly!
        self.assertEqual(id(timeline._waveform_pixmap_cache[1]), pix_id)

        # 3. New waveform data invalidates cache
        timeline.set_waveform_data([0.2, 0.4], 10.0)
        self.assertIsNone(timeline._waveform_pixmap_cache)

        # 4. reset_timeline invalidates cache
        timeline.set_waveform_data(samples, 10.0)
        p3 = QPainter(canvas)
        timeline._draw_waveform(p3, 0, 10.0, 500, 60.0, 600)
        p3.end()
        self.assertIsNotNone(timeline._waveform_pixmap_cache)
        timeline.reset_timeline()
        self.assertIsNone(timeline._waveform_pixmap_cache)

    def test_video_thumbnails_pixmap_cache_and_reuse(self):
        """Verify _draw_video_thumbnails populates _thumbnails_pixmap_cache and reuses it."""
        timeline = self.timeline
        timeline.set_duration(10.0)

        thumb_pix1 = QPixmap(64, 48)
        thumb_pix1.fill(QColor("#ff0000"))
        thumb_pix2 = QPixmap(64, 48)
        thumb_pix2.fill(QColor("#0000ff"))
        thumbnails = [(0.0, thumb_pix1), (5.0, thumb_pix2)]

        timeline.set_video_thumbnails(thumbnails)
        self.assertIsNone(timeline._thumbnails_pixmap_cache)

        # 1. Render thumbnail strip
        canvas = QPixmap(600, 100)
        p = QPainter(canvas)
        timeline._draw_video_thumbnails(p, 0, 10.0, 500, 60.0, 600)
        p.end()

        # Cache must be populated
        self.assertIsNotNone(timeline._thumbnails_pixmap_cache)
        cache_key, cached_pix = timeline._thumbnails_pixmap_cache
        self.assertIsInstance(cached_pix, QPixmap)
        self.assertFalse(cached_pix.isNull())
        pix_id = id(cached_pix)

        # 2. Render again
        canvas2 = QPixmap(600, 100)
        p2 = QPainter(canvas2)
        timeline._draw_video_thumbnails(p2, -30, 10.0, 500, 60.0, 600)
        p2.end()

        # Cached pixmap instance should be reused
        self.assertEqual(id(timeline._thumbnails_pixmap_cache[1]), pix_id)

        # 3. Setting new thumbnails invalidates cache
        timeline.set_video_thumbnails([(0.0, thumb_pix1)])
        self.assertIsNone(timeline._thumbnails_pixmap_cache)

    def test_snap_guide_line_painted_during_drag(self):
        """Verify snap guide line renders without error when dragging with snap active."""
        timeline = self.timeline
        timeline.set_duration(20.0)
        timeline.resize(800, 400)

        timeline._drag_mode = "move"
        timeline._snap_guide_time = 2.5

        # Render paintEvent to verify crisp cyan snap line executes cleanly
        canvas = QPixmap(800, 400)
        p = QPainter(canvas)
        timeline.render(p)
        p.end()
        self.assertFalse(canvas.isNull())

    def test_subtitle_and_overlays_never_spill_outside_view_bounds(self):
        """Create a container (800x600) with child MpvVideoView (400x300 at (100, 100)),
        test extreme positions, and assert subtitle_item stays strictly inside MpvVideoView.rect()."""
        container = QWidget()
        container.resize(800, 600)
        view = MpvVideoView(container, native_surface=False)
        view.setGeometry(100, 100, 400, 300)
        container.show()
        view.show()
        self.app.processEvents()

        view.set_video_dimensions(1920, 1080)
        view.subtitle_item.set_text("Preview Subtitle Text For Boundary Spill Check")
        view.subtitle_item.custom_position_enabled = True

        for x_pct in (0, 100):
            for y_pct in (0, 100):
                view.subtitle_item.custom_x_percent = x_pct
                view.subtitle_item.custom_y_percent = y_pct
                view.reposition_subtitle()

                sub_geom = view.subtitle_item.geometry()
                top_left_in_view = view.mapFromGlobal(sub_geom.topLeft())
                rect_in_view = QRect(top_left_in_view, sub_geom.size())
                view_rect = view.rect()

                # Zero pixel spill outside view bounds
                self.assertTrue(
                    view_rect.contains(rect_in_view),
                    f"Subtitle spilled outside MpvVideoView bounds at ({x_pct}%, {y_pct}%): {rect_in_view} not in {view_rect}"
                )
                self.assertGreaterEqual(rect_in_view.left(), view_rect.left())
                self.assertLessEqual(rect_in_view.right(), view_rect.right())
                self.assertGreaterEqual(rect_in_view.top(), view_rect.top())
                self.assertLessEqual(rect_in_view.bottom(), view_rect.bottom())

        view.subtitle_item.close()
        view.subtitle_item.deleteLater()
        view.close()
        view.deleteLater()
        container.close()
        container.deleteLater()
        self.app.processEvents()

    def test_overlays_suppressed_when_popup_or_modal_open(self):
        """Patch QApplication.activePopupWidget, call reposition_subtitle and blur sync_to_view,
        and assert both overlays are hidden."""
        container = QWidget()
        view = MpvVideoView(container, native_surface=False)
        container.show()
        container.activateWindow()
        view.show()
        view.set_video_dimensions(1920, 1080)
        view.subtitle_item.set_text("Visible Subtitle")
        view.set_blur_edit_enabled(True)
        view.blur_overlay.show()
        view.subtitle_item.show()

        dummy_popup = QWidget()
        with patch.object(QApplication, "activePopupWidget", return_value=dummy_popup):
            view.reposition_subtitle()
            view.blur_overlay.sync_to_view()
            self.assertFalse(view.subtitle_item.isVisible())
            self.assertFalse(view.blur_overlay.isVisible())

        view.blur_overlay.close()
        view.blur_overlay.deleteLater()
        view.subtitle_item.close()
        view.subtitle_item.deleteLater()
        view.close()
        view.deleteLater()
        container.close()
        container.deleteLater()
        self.app.processEvents()

    def test_deterministic_overlay_stack_order_raises_editable_layer(self):
        """Attach blur_overlay, logo_overlay, text_overlay, and subtitle_item, mock raise_,
        enable set_blur_edit_enabled(True), and verify blur_overlay.raise_ is called last."""
        container = QWidget()
        view = MpvVideoView(container, native_surface=False)
        container.show()
        container.activateWindow()
        view.show()
        view.set_video_dimensions(1920, 1080)

        view.set_logo("", 0.1, 0.1, 0.2, 0.2)
        view.set_logo_editable(False)
        view.set_text_layers([{"id": "txt1", "text": "Sample Text", "x": 100, "y": 100, "visible": True}])
        view.set_text_layer_editable(False)
        view.set_subtitle_editable(False)
        view.set_blur_edit_enabled(True)

        view.blur_overlay.show()
        view.logo_overlay.show()
        view.text_overlay.show()
        view.subtitle_item.show()

        raise_calls = []
        view.blur_overlay.raise_ = MagicMock(side_effect=lambda: raise_calls.append("blur"))
        view.logo_overlay.raise_ = MagicMock(side_effect=lambda: raise_calls.append("logo"))
        view.text_overlay.raise_ = MagicMock(side_effect=lambda: raise_calls.append("text"))
        view.subtitle_item.raise_ = MagicMock(side_effect=lambda: raise_calls.append("subtitle"))

        view._sync_overlay_stack_order()

        self.assertIn("blur", raise_calls)
        self.assertEqual(raise_calls[-1], "blur", f"Expected 'blur' to be raised last, got order: {raise_calls}")

        for overlay in (view.blur_overlay, getattr(view, "logo_overlay", None), getattr(view, "text_overlay", None), getattr(view, "subtitle_item", None)):
            if overlay is not None:
                overlay.close()
                overlay.deleteLater()
        view.close()
        view.deleteLater()
        container.close()
        container.deleteLater()
        self.app.processEvents()

    def test_destroying_mpv_video_view_does_not_raise_shiboken_error(self):
        """Attach overlays, delete view with shiboken6, then call sync_to_view on all overlays
        and verify no RuntimeError is raised."""
        import gc
        try:
            import shiboken6
        except ImportError:
            shiboken6 = None

        container = QWidget()
        view = MpvVideoView(container, native_surface=False)
        container.show()
        container.activateWindow()
        view.show()
        view.set_video_dimensions(1920, 1080)
        view.set_logo("", 0.1, 0.1, 0.2, 0.2)
        view.set_text_layers([{"id": "txt1", "text": "Sample", "x": 100, "y": 100, "visible": True}])
        view.set_blur_edit_enabled(True)

        blur_overlay = view.blur_overlay
        mask_overlay = view.mask_overlay
        logo_overlay = view.logo_overlay
        text_overlay = view.text_overlay
        subtitle_item = view.subtitle_item

        view.setParent(None)

        if shiboken6 is not None:
            shiboken6.delete(view)
        else:
            view.deleteLater()
            self.app.processEvents()

        for overlay in (blur_overlay, mask_overlay, logo_overlay, text_overlay, subtitle_item):
            try:
                overlay.sync_to_view()
            except RuntimeError as exc:
                self.fail(f"{type(overlay).__name__}.sync_to_view() raised RuntimeError: {exc}")
            overlay.close()
            overlay.deleteLater()

        container.close()
        container.deleteLater()
        self.app.processEvents()
        gc.collect()

    def test_subtitle_drag_uses_qt_overlay_without_mpv_ass_reload_until_commit(self):
        """Verify paused subtitle editing uses instant 120 FPS Qt overlay without reloading MPV ASS."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._preview_is_playing.return_value = False
        gui._subtitle_track_preview_visible = True
        gui._preview_video_has_burned_subtitles = False
        gui._use_libass_live_preview = True
        gui._loaded_live_ass_path = ""
        gui._loaded_live_ass_signature = None
        gui._live_preview_signature = "sig1"

        player = MagicMock()
        player._sub_track_id = 1
        player.timeline_position_ms.return_value = 1000
        gui.media_player = player

        subtitle_item = MagicMock()
        subtitle_item._editable = True
        subtitle_item._drag_active = False
        subtitle_item.render_text = False
        def _set_editable(val):
            subtitle_item._editable = bool(val)
        subtitle_item.set_editable.side_effect = _set_editable

        gui.video_view = MagicMock()
        gui.video_view.subtitle_item = subtitle_item

        def _set_rendering(val):
            subtitle_item.render_text = bool(val)
        gui._set_subtitle_item_text_rendering.side_effect = _set_rendering
        gui.sync_live_subtitle_preview.side_effect = lambda: VideoTranslatorGUI.sync_live_subtitle_preview(gui)

        # 1. While subtitle is editable (paused edit mode), sync_live_subtitle_preview
        # must switch to Qt overlay, clear MPV subtitle, and NEVER call set_subtitle_file.
        VideoTranslatorGUI.sync_live_subtitle_preview(gui)
        player.clear_subtitle.assert_called()
        self.assertTrue(subtitle_item.render_text)
        player.set_subtitle_file.assert_not_called()
        gui._write_live_preview_assets.assert_not_called()

        # 2. Deselecting / review mode commits the ASS file back to MPV
        gui._resolve_live_preview_segments.return_value = ([{"start": 0, "end": 2, "text": "hello"}], "editor")
        gui._write_live_preview_assets.return_value = ("test.srt", "test.ass")
        player.reset_mock()
        gui._write_live_preview_assets.reset_mock()

        with patch("os.path.exists", return_value=True):
            VideoTranslatorGUI.prepare_preview_for_review_mode(gui)
            self.assertFalse(subtitle_item._editable)
            gui._write_live_preview_assets.assert_called_once()
            player.set_subtitle_file.assert_called_once_with("test.ass")
            self.assertFalse(subtitle_item.render_text)

    def test_preview_mux_worker_never_burns_subtitles_into_preview_video(self):
        """Verify PreviewController.preview_video keeps _preview_video_has_burned_subtitles=False
        and passes render_subtitles=False to PreviewMuxWorker even in subtitle mode with active video filters."""
        from ui.controllers.preview_controller import PreviewController

        gui = MagicMock()
        gui.preview_thread = None
        gui.video_path_edit.text.return_value = "C:/test/sample.mp4"
        gui.get_output_mode_key.return_value = "subtitle"
        gui.resolve_selected_audio_path.return_value = ""
        gui.has_active_video_filters.return_value = True
        gui.has_active_overlay_layers.return_value = False
        gui.video_time_warps = []
        gui.last_preview_video_path = ""
        gui.last_exact_preview_5s_path = ""
        gui.get_output_scale_mode_key.return_value = "fit"
        gui.get_output_fill_focus.return_value = (0.5, 0.5)
        gui.get_video_filter_state.return_value = {"brightness": 1.1}
        gui.get_project_temp_path.return_value = "C:/test/temp/preview_vi_voice.mp4"
        gui.get_project_temp_dir.return_value = "C:/test/temp"
        gui.get_subtitle_export_style.return_value = {}

        controller = PreviewController(gui)

        with patch("os.path.exists", return_value=True), \
             patch.object(controller, "build_full_active_subtitle_srt", return_value=("sub.srt", [{"start": 0, "end": 1}])), \
             patch.object(controller, "_resolve_output_canvas_dimensions", return_value=(1920, 1080)), \
             patch("ui.controllers.preview_controller.PreviewMuxWorker") as mock_worker_cls:
            mock_worker = MagicMock()
            mock_worker_cls.return_value = mock_worker

            controller.preview_video()

            self.assertFalse(gui._preview_video_has_burned_subtitles)
            mock_worker_cls.assert_called_once()
            _, kwargs = mock_worker_cls.call_args
            self.assertFalse(kwargs.get("render_subtitles"))

    def test_paused_layers_show_passive_frames_and_badges_without_mpv_filter(self):
        """When paused, setting blur and mask regions with editable=False keeps overlays visible
        and click-through, and painting them does not raise. In review mode (playing), they hide."""
        container = QWidget()
        container.resize(800, 600)
        view = MpvVideoView(container, native_surface=False)
        view.setGeometry(100, 100, 400, 300)
        container.show()
        view.show()
        view.set_video_dimensions(1920, 1080)
        self.app.processEvents()

        view.set_blur_regions_normalized([{"x": 0.2, "y": 0.2, "width": 0.3, "height": 0.2}])
        view.set_blur_edit_enabled(False)

        view.set_mask_regions(
            [{"x": 0.4, "y": 0.4, "width": 0.3, "height": 0.2}],
            active_index=0,
            editable=False,
        )

        self.app.processEvents()

        # Both overlays should be visible while paused even though editable is False
        self.assertTrue(view.blur_overlay.isVisible())
        self.assertTrue(view.mask_overlay.isVisible())
        self.assertTrue(view.blur_overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(view.mask_overlay.testAttribute(Qt.WA_TransparentForMouseEvents))

        # Painting them must succeed without error
        for overlay in (view.blur_overlay, view.mask_overlay):
            pix = QPixmap(max(1, overlay.width()), max(1, overlay.height()))
            overlay.render(pix)

        # Review mode (playback simulation) hides the blur and mask overlay windows
        view.set_overlays_review_mode(True)
        self.app.processEvents()
        self.assertFalse(view.blur_overlay.isVisible())
        self.assertFalse(view.mask_overlay.isVisible())

        # Exiting review mode (pause simulation) restores them
        view.set_overlays_review_mode(False)
        self.app.processEvents()
        self.assertTrue(view.blur_overlay.isVisible())
        self.assertTrue(view.mask_overlay.isVisible())

        # Cleanup
        for overlay in (view.blur_overlay, view.mask_overlay, view.subtitle_item):
            overlay.close()
            overlay.deleteLater()
        view.close()
        view.deleteLater()
        container.close()
        container.deleteLater()
        self.app.processEvents()

    def test_blur_and_mask_mpv_filters_only_applied_when_playing_or_forced(self):
        """Verify apply_preview_blur_region and _apply_mask_to_preview clear MPV filter when paused,
        and apply when playing or force=True."""
        from ui.main_window import VideoTranslatorGUI

        gui = MagicMock()
        gui._blur_effect_enabled.return_value = True
        gui._preview_is_playing.return_value = False
        gui._deferred_effect_layer_id_for.return_value = ""
        gui._current_blur_regions_payload.return_value = [{"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2}]
        gui._current_mask_regions_payload.return_value = [{"x": 0.3, "y": 0.3, "width": 0.2, "height": 0.2}]
        gui._mask_track_preview_visible = True

        player = MagicMock()
        gui.media_player = player
        video_view = MagicMock()
        gui.video_view = video_view

        # 1. While paused (force=False), MPV filters must be cleared
        VideoTranslatorGUI.apply_preview_blur_region(gui, force=False)
        player.clear_blur_region.assert_called_once()
        player.set_blur_region.assert_not_called()

        VideoTranslatorGUI._apply_mask_to_preview(gui, force=False)
        player.clear_mask_region.assert_called_once()
        player.set_mask_region.assert_not_called()

        # 2. When forced (force=True), MPV filters are applied even while paused
        player.reset_mock()
        VideoTranslatorGUI.apply_preview_blur_region(gui, force=True)
        player.set_blur_region.assert_called_once()
        player.clear_blur_region.assert_not_called()

        VideoTranslatorGUI._apply_mask_to_preview(gui, force=True)
        player.set_mask_region.assert_called_once()
        player.clear_mask_region.assert_not_called()

        # 3. While playing (_preview_is_playing() == True), MPV filters are applied with force=False
        player.reset_mock()
        gui._preview_is_playing.return_value = True
        VideoTranslatorGUI.apply_preview_blur_region(gui, force=False)
        player.set_blur_region.assert_called_once()
        player.clear_blur_region.assert_not_called()

        VideoTranslatorGUI._apply_mask_to_preview(gui, force=False)
        player.set_mask_region.assert_called_once()
        player.clear_mask_region.assert_not_called()

    def test_alt_tab_window_deactivate_and_activate_restores_all_overlays(self):
        """Verify that WindowDeactivate hides overlays, but focusing an own overlay
        does not suppress them, and WindowActivate fully restores all active overlays
        (subtitle, text, logo, blur, mask)."""
        container = QWidget()
        container.resize(800, 600)
        view = MpvVideoView(container, native_surface=False)
        view.setGeometry(50, 50, 700, 500)
        external_win = QWidget()

        try:
            with patch.object(QApplication, "activeWindow", return_value=container):
                container.show()
                view.show()
                view.set_video_dimensions(1920, 1080)
                self.app.processEvents()

                # Set up subtitle
                view.subtitle_item.set_text("Active Subtitle Text")
                view.reposition_subtitle()
                view.subtitle_item.show()

                # Set up text overlay
                view.set_text_layers([{"id": "t1", "text": "Active Text", "x": 0.1, "y": 0.1, "width": 0.3, "height": 0.1}])

                # Set up logo overlay
                view.set_logos([{"id": "l1", "source": "", "x": 0.1, "y": 0.5, "width": 0.2, "height": 0.2, "opacity": 1.0, "rotation": 0.0}])

                # Set up blur overlay
                view.set_blur_regions_normalized([{"x": 0.2, "y": 0.2, "width": 0.2, "height": 0.2}])

                # Set up mask overlay
                view.set_mask_regions([{"x": 0.5, "y": 0.2, "width": 0.2, "height": 0.2}], active_index=0, editable=True)

                self.app.processEvents()

                # Confirm all 5 overlays are initially visible
                self.assertTrue(view.subtitle_item.isVisible())
                self.assertTrue(view.text_overlay.isVisible())
                self.assertTrue(view.logo_overlay.isVisible())
                self.assertTrue(view.blur_overlay.isVisible())
                self.assertTrue(view.mask_overlay.isVisible())

            # 1. When focus moves to an own overlay (e.g. blur_overlay), overlays must NOT be suppressed
            with patch.object(QApplication, "activeWindow", return_value=view.blur_overlay):
                deact_event = QEvent(QEvent.WindowDeactivate)
                QApplication.sendEvent(container, deact_event)
                self.app.processEvents()
                self.assertTrue(view.subtitle_item.isVisible())
                self.assertTrue(view.blur_overlay.isVisible())
                self.assertFalse(view.blur_overlay._suspended)

            # 2. When focus moves outside (external app WindowDeactivate), overlays must be hidden / suspended
            with patch.object(QApplication, "activeWindow", return_value=external_win):
                deact_event = QEvent(QEvent.WindowDeactivate)
                QApplication.sendEvent(container, deact_event)
                self.app.processEvents()
                self.assertFalse(view.subtitle_item.isVisible())
                self.assertFalse(view.text_overlay.isVisible())
                self.assertFalse(view.logo_overlay.isVisible())
                self.assertFalse(view.blur_overlay.isVisible())
                self.assertFalse(view.mask_overlay.isVisible())
                self.assertTrue(view.blur_overlay._suspended)
                self.assertTrue(view.mask_overlay._suspended)
                self.assertTrue(view.logo_overlay._suspended)
                self.assertTrue(view.text_overlay._suppressed)

            # 3. When Alt-Tabbing back (WindowActivate on container), _restore_subtitle_overlay restores all overlays
            with patch.object(QApplication, "activeWindow", return_value=container):
                act_event = QEvent(QEvent.WindowActivate)
                QApplication.sendEvent(container, act_event)
                view._restore_subtitle_overlay()
                self.app.processEvents()

                self.assertTrue(view.subtitle_item.isVisible())
                self.assertTrue(view.text_overlay.isVisible())
                self.assertTrue(view.logo_overlay.isVisible())
                self.assertTrue(view.blur_overlay.isVisible())
                self.assertTrue(view.mask_overlay.isVisible())
                self.assertFalse(view.blur_overlay._suspended)
                self.assertFalse(view.mask_overlay._suspended)
                self.assertFalse(view.logo_overlay._suspended)
                self.assertFalse(view.text_overlay._suppressed)
        finally:
            # Cleanup
            external_win.deleteLater()
            for overlay in (getattr(view, "subtitle_item", None), getattr(view, "text_overlay", None),
                            getattr(view, "logo_overlay", None), getattr(view, "blur_overlay", None), getattr(view, "mask_overlay", None)):
                if overlay is not None:
                    try:
                        overlay.close()
                        overlay.deleteLater()
                    except Exception:
                        pass
            try:
                view.close()
                view.deleteLater()
            except Exception:
                pass
            try:
                container.close()
                container.deleteLater()
            except Exception:
                pass
            self.app.processEvents()

    def test_unapplied_fit_voice_overflow_clamps_playhead_and_stops_at_eof(self):
        """Verify that when TTS audio extends past video duration and video_time_warps is empty:
        1. _on_native_audio_position_changed clamps position to duration (1668 ms).
        2. EditorTimeline.set_playhead clamps playhead to duration (1.668s).
        3. _poll_state EOF transition pauses _native_audio_engine immediately without flicker."""
        from types import SimpleNamespace
        from PySide6.QtMultimedia import QMediaPlayer
        from ui.utils.media_backend import MpvMediaPlayerBackend

        # 1. Test position clamping in _on_native_audio_position_changed
        backend = SimpleNamespace(
            _native_audio_active=True,
            _duration_ms=1668,
            _video_time_warps=[],
            _position_ms=1668,
            _dubbed_position_ms=1668,
            _is_warped_preview=lambda: False,
            _state=QMediaPlayer.PausedState,
            _last_audio_pos_emit_frame=None,
            _native_audio_engine=MagicMock(),
            positionChanged=MagicMock(),
        )
        MpvMediaPlayerBackend._on_native_audio_position_changed(backend, 1850)
        self.assertEqual(backend._dubbed_position_ms, 1668)
        self.assertEqual(backend._position_ms, 1668)
        backend._native_audio_engine.pause.assert_called_once()

        MpvMediaPlayerBackend._on_native_audio_position_changed(backend, 1985)
        self.assertEqual(backend._dubbed_position_ms, 1668)
        self.assertEqual(backend._position_ms, 1668)

        # 2. Test timeline playhead clamping
        self.timeline.set_duration_ms(1668)
        self.timeline.set_playhead(1.95)
        self.assertAlmostEqual(self.timeline.playhead, 1.668, places=3)

        # 3. Test EOF transition in _poll_state pauses _native_audio_engine
        mock_engine = MagicMock()
        def mock_read_prop(prop, *args, default=None):
            if prop in ("time-pos", "time_pos"):
                return 1.668
            if prop == "duration":
                return 1.668
            if prop == "pause":
                return True
            if prop in ("eof-reached", "eof_reached"):
                return True
            if prop in ("core-idle", "core_idle"):
                return False
            if prop == "seeking":
                return False
            return default

        backend_poll = SimpleNamespace(
            _source_path="dummy.mp4",
            _read_property=mock_read_prop,
            _state=QMediaPlayer.PlayingState,
            _duration_ms=1668,
            _position_ms=1668,
            _native_audio_active=True,
            _native_audio_engine=mock_engine,
            _original_loaded_path="",
            _dubbed_loaded_path="",
            _video_frozen=False,
            _is_warped_preview=lambda: False,
            _video_time_warps=[],
            _update_video_speed_for_time_warps=lambda t: None,
            _last_seek_mono=0.0,
            _pause_all_audio_sidecars=lambda: MpvMediaPlayerBackend._pause_all_audio_sidecars(backend_poll),
            stateChanged=MagicMock(),
            durationChanged=MagicMock(),
            positionChanged=MagicMock(),
            _effective_timeline_duration_ms=lambda: MpvMediaPlayerBackend._effective_timeline_duration_ms(backend_poll),
        )
        MpvMediaPlayerBackend._poll_state(backend_poll)
        mock_engine.pause.assert_called_once()
        self.assertEqual(backend_poll._state, QMediaPlayer.PausedState)

    def test_preview_audio_worker_stops_at_max_end_ms(self):
        """Verify _PreviewAudioWorker computes _max_end_ms and cleanly stops timer
        and clamps _timeline_pos_exact_ms at EOF instead of advancing past max_end_ms."""
        from ui.utils.preview_audio import _PreviewAudioWorker

        worker = _PreviewAudioWorker(sample_rate=16000, block_size=160)
        # 1. set_tracks computes _max_end_ms
        tracks = [
            {"id": "A1", "path": "nonexistent_a.wav", "end_ms": 1200},
            {"id": "TS1", "path": "nonexistent_b.wav", "end_ms": 1668},
        ]
        with patch("os.path.exists", return_value=True), patch("os.stat"), patch("ui.utils.preview_audio.AudioReader"):
            worker.set_tracks(tracks)
        self.assertEqual(worker._max_end_ms, 1668)

        # 2. _on_timer_tick stops timer and clamps at max_end_ms
        worker._sink = MagicMock()
        worker._sink.bytesFree.return_value = 10000
        worker._sink.bufferSize.return_value = 20000
        worker._io_device = MagicMock()
        worker._is_playing = True
        worker._timer = MagicMock()
        worker._timer.isActive.return_value = True
        worker._timeline_pos_exact_ms = 1668.0

        state_changes = []
        worker.stateChanged.connect(state_changes.append)
        pos_changes = []
        worker.positionChanged.connect(pos_changes.append)

        worker._on_timer_tick()

        self.assertFalse(worker._is_playing)
        worker._timer.stop.assert_called_once()
        self.assertEqual(worker._timeline_pos_exact_ms, 1668.0)
        self.assertIn(1668, pos_changes)
        self.assertIn(2, state_changes)


if __name__ == "__main__":
    unittest.main()
