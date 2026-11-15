"""Large-project layout, cache invalidation and narrow-update regressions."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(folder))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QSignalSpy
from PySide6.QtGui import QPainter, QPixmap
from app.layers.base import LayerType
from app.layers.timeline import Track
from ui.views.editor.timeline import EditorTimeline


class TimelineRefreshTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.timeline = EditorTimeline()
        self.rows = [dict(start=i * 2., end=i * 2. + 1.8, text=f"Cue {i}") for i in range(1000)]
        self.timeline.set_segments(self.rows)

    def tearDown(self):
        self.timeline.close()
        self.timeline.deleteLater()
        self.app.processEvents()

    def test_batch_computes_one_layout_and_paint_reuses_it(self):
        timeline = self.timeline
        events = QSignalSpy(timeline.layoutChanged)
        with patch.object(timeline, "_compute_overlap_rows", wraps=timeline._compute_overlap_rows) as overlap:
            with timeline.batch_update():
                timeline.set_segments(self.rows)
                with timeline.batch_update():
                    timeline.set_video_time_warps([dict(type="freeze", time=5., duration=1.)])
                    timeline._redraw()
            self.assertEqual(events.count(), 1)
            self.assertEqual(overlap.call_count, 1)
            track = next(track for track in timeline._timeline.tracks if track.name == "TS1")
            pixmap = QPixmap(800, 200)
            painter = QPainter(pixmap)
            timeline._draw_track_layers(painter, track, 0, 30, 80)
            painter.end()
            self.assertEqual(overlap.call_count, 1)

    def test_batch_exception_releases_pending_layout(self):
        timeline = self.timeline
        events = QSignalSpy(timeline.layoutChanged)
        with self.assertRaises(ValueError):
            with timeline.batch_update():
                timeline.set_segments(self.rows[:3])
                raise ValueError("interrupted update")
        self.assertEqual(events.count(), 1)
        self.assertEqual(timeline._batch_depth, 0)
        timeline.set_segments(self.rows[:2])
        self.assertEqual(events.count(), 2)

    def test_warps_preserve_cue_layout_and_detect_in_place_input_change(self):
        timeline = self.timeline
        track = next(track for track in timeline._timeline.tracks if track.name == "TS1")
        cached = timeline._overlap_layout_cache[track.id]
        events = QSignalSpy(timeline.layoutChanged)
        warps = [dict(type="freeze", time=5., duration=1.)]
        timeline.set_video_time_warps(warps)
        timeline.set_video_time_warps(warps)
        warps[0]["duration"] = 2.
        timeline.set_video_time_warps(warps)
        self.assertEqual(events.count(), 0)
        self.assertIs(timeline._overlap_layout_cache[track.id], cached)
        self.assertEqual(timeline.get_video_time_warps()[0]["duration"], 2.)

    def test_overlay_layout_and_appearance_updates_keep_subtitle_geometry(self):
        from app.layers.blur import BlurLayer
        timeline = self.timeline
        blur = Track(name="B1", type=LayerType.BLUR, layers=[BlurLayer(start=0., end=5.)])
        timeline._timeline.tracks.append(blur)
        timeline._redraw()
        subtitle = next(track for track in timeline._timeline.tracks if track.name == "TS1")
        cached = timeline._overlap_layout_cache[subtitle.id]
        with patch.object(timeline, "_compute_overlap_rows", wraps=timeline._compute_overlap_rows) as overlap:
            blur.layers[0].end = 10.
            timeline.redraw_track(blur)
            subtitle.locked = True
            timeline.refresh_track_appearance()
            self.assertEqual(overlap.call_count, 0)
            self.assertIs(timeline._overlap_layout_cache[subtitle.id], cached)

    def test_text_only_edit_preserves_identity_layout_and_tts_dirty_flag(self):
        timeline = self.timeline
        track = next(track for track in timeline._timeline.tracks if track.name == "TS1")
        layer = track.layers[200]
        cached = timeline._overlap_layout_cache[track.id]
        events = QSignalSpy(timeline.layoutChanged)
        self.assertTrue(timeline.update_subtitle_segment(200, dict(self.rows[200], text="Updated", _tts_dirty=True)))
        self.assertIs(track.layers[200], layer)
        self.assertEqual(layer.text, "Updated")

        self.assertEqual(layer.dub_text, "Updated")
        self.assertTrue(layer.metadata["_tts_dirty"])
        self.assertEqual(events.count(), 0)
        self.assertIs(timeline._overlap_layout_cache[track.id], cached)
        self.assertFalse(timeline.update_subtitle_segment(200, dict(self.rows[200], start=399., text="Timing changed")))
        self.assertEqual(layer.text, "Updated")

    def test_audio_overflow_rebuilds_overlap_height_after_explicit_geometry_refresh(self):
        timeline = self.timeline
        track = next(track for track in timeline._timeline.tracks if track.name == "TS1")
        self.assertEqual(timeline._track_heights[track.id], timeline.CHILD_TRACK_H)
        track.layers[0].metadata["_audio_end"] = 3.8
        timeline._rebuild_track_heights()
        self.assertEqual(timeline._track_heights[track.id], 2 * timeline.CHILD_TRACK_H)

    def test_repeated_active_cue_and_same_playhead_pixel_skip_work(self):
        timeline = self.timeline
        timeline.set_active_segment_index(200)
        class Indices(dict):
            def items(self):
                raise AssertionError("Repeated cue should not rescan all indices")
        timeline._segment_indices = Indices(timeline._segment_indices)
        timeline.set_active_segment_index(200)
        timeline._segment_indices = dict(timeline._segment_indices)
        # A manual-selection change must invalidate the quick return.
        timeline._manual_subtitle_selection = True
        timeline.set_active_segment_index(0)
        selected = timeline._selected_layer_id
        timeline._manual_subtitle_selection = False
        timeline.set_active_segment_index(0)
        self.assertNotEqual(timeline._selected_layer_id, selected)
        with patch.object(timeline.viewport(), "update") as update:
            timeline.set_playhead(1.)
            update.reset_mock()
            timeline.set_playhead(1.001)
            self.assertEqual(update.call_count, 0)
            timeline.set_playhead(1.1)
            self.assertEqual(update.call_count, 1)

    def test_paint_skips_tracks_outside_vertical_viewport(self):
        timeline = self.timeline
        timeline._timeline.tracks = [Track(name=f"Track {i}", type=LayerType.AUDIO, height=56) for i in range(100)]
        timeline._redraw()
        timeline.resize(800, 220)
        timeline.show()
        self.app.processEvents()
        with patch.object(timeline, "_draw_track_layers", wraps=timeline._draw_track_layers) as draw:
            timeline.viewport().repaint()
            self.assertLess(draw.call_count, 10)
            self.assertGreater(draw.call_count, 0)
            draw.reset_mock()
            timeline.verticalScrollBar().setValue(timeline.verticalScrollBar().maximum())
            timeline.viewport().repaint()
            drawn = [call.args[1] for call in draw.call_args_list]
            self.assertIn(timeline._timeline.tracks[-1], drawn)
            self.assertNotIn(timeline._timeline.tracks[0], drawn)


if __name__ == "__main__":
    unittest.main()
