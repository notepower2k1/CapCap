import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

import test_performance_queue as harness
from app.capcut.draft_export import DraftExporter
from app.layers.base import LayerType
from app.layers.image import ImageLayer
from app.layers.mask import MaskLayer
from app.layers.timeline import Track
from app.layers.transform import Transform
from app.video_processor import embed_ass_subtitles
from app.workflows.export_workflow import ExportWorkflow
from runtime_paths import bin_path
from ui.controllers.capcut_export_controller import capture_snapshot


class OverlayStackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PerformanceQueueTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        harness.PerformanceQueueTests.tearDownClass()

    def test_subtitle_refresh_does_not_raise_a_covered_logo_again(self):
        from widgets import mpv_video_view

        events = []

        def overlay(name):
            return SimpleNamespace(isVisible=Mock(return_value=True), installEventFilter=Mock(),
                                   raise_=lambda: events.append(name), hide=Mock())

        view = SimpleNamespace(overlay_order=["logo", "mask", "text"], logo_overlay=overlay("logo"),
                               mask_overlay=overlay("mask"), subtitle_item=overlay("subtitle"))
        sync = mpv_video_view.MpvVideoView._sync_overlay_stack_order
        with patch.object(mpv_video_view, "_is_valid_qobject", return_value=True), \
                patch.object(mpv_video_view, "_should_suppress_overlay", return_value=False), \
                patch.object(mpv_video_view, "_is_own_popup_menu", return_value=False):
            sync(view)
            self.assertEqual(events, ["logo", "mask", "subtitle"])
            events.clear()
            for visible in (False, True, True, False, True):
                view.subtitle_item.isVisible.return_value = visible
                sync(view)
            self.assertEqual(events, ["subtitle"] * 3)

            events.clear()
            view.overlay_order = ["mask", "logo", "text"]
            sync(view)
            self.assertEqual(events, ["mask", "logo", "subtitle"])

    def test_drop_changes_render_order_and_round_trips(self):
        host = harness.PerformanceQueueTests()
        host.setUp()
        self.addCleanup(host.tearDown)
        source = host.source()
        gui = host.editor(source)
        gui.on_add_timeline_layer("blur")
        blur = next(layer for layer in gui.timeline._timeline.all_layers() if layer.type == LayerType.BLUR)
        self.assertGreater(blur.position_y, .75)
        img = host.root / "red.png"
        Image.new("RGB", (32, 32), "red").save(img)
        model = gui.timeline._timeline
        model.tracks.extend([
            Track(name="L1 Logo", type=LayerType.IMAGE, layers=[
                ImageLayer(source=str(img), end=1, transform=Transform(x=25, y=25, scale_x=2.5, scale_y=2.5)),
            ]),
            Track(name="M1", type=LayerType.MASK, layers=[
                MaskLayer(position_x=.25, position_y=.25, width=.5, height=.5, color="#00FF00", end=1),
            ]),
        ])
        gui.timeline._redraw()
        gui._sync_track_labels()
        bar = gui.track_label_bar
        start = QPoint(12, bar._track_top(bar._track_names.index("M1")) + 8)
        end = QPoint(12, bar._track_top(bar._track_names.index("L1 Logo")) + 8)
        QTest.mousePress(bar, Qt.LeftButton, pos=start)
        QTest.mouseMove(bar, end)
        self.assertEqual(model.overlay_order, [])
        QTest.mouseRelease(bar, Qt.LeftButton, pos=end)
        self.assertEqual(model.overlay_order[:2], ["logo", "mask"])
        order_before_pin_attempt = list(model.overlay_order)
        gui.move_overlay_track("B1", "M1", True)
        self.assertEqual(model.overlay_order, order_before_pin_attempt)
        self.assertEqual(gui.video_view.overlay_order, model.overlay_order)
        self.assertTrue(gui.video_view.mask_overlay._mask_fills)
        self.assertTrue(gui.undo_last_timeline_timing_edit())
        self.assertEqual(model.overlay_order, [])
        self.assertTrue(gui.redo_last_timeline_timing_edit())

        ass = host.root / "empty.ass"
        ass.write_text("[Script Info]\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n[Events]\n"
                       "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n", encoding="utf-8")
        ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
        masks, logos, _, _ = ExportWorkflow(str(host.root))._extract_overlay_layers(gui.current_project_state)

        def rendered_center(order):
            dest = host.root / ("-".join(order) + ".mp4")
            cfg = dict(gui.get_video_filter_state(), overlay_order=order)
            self.assertTrue(embed_ass_subtitles(str(source), str(ass), str(dest), ffmpeg_path=ffmpeg,
                                               mask_regions=masks, logo_layers=logos, video_filter_state=cfg))
            pixels = subprocess.check_output([ffmpeg, "-v", "error", "-i", str(dest), "-frames:v", "1",
                                             "-pix_fmt", "rgb24", "-f", "rawvideo", "-"])
            offset = (90 * 320 + 160) * 3
            return pixels[offset:offset+3]

        self.assertGreater(rendered_center(["logo", "mask", "text"])[1], 220)
        self.assertGreater(rendered_center(["mask", "logo", "text"])[0], 220)
        out = host.root / "draft"
        DraftExporter(ffmpeg=ffmpeg, ffprobe=bin_path("ffmpeg", "ffprobe.exe")).export(capture_snapshot(gui), out)
        tracks = json.loads((out / "draft_content.json").read_text(encoding="utf-8"))["tracks"]
        order = [track["name"] for track in tracks]
        self.assertLess(order.index("L1 Logo"), order.index("M1 Overlay"))
        recipe = gui.video_layout_controller.saved.capture()
        self.assertEqual(recipe["overlay_order"], model.overlay_order)
        expected = list(model.overlay_order)
        gui.load_project_context(gui.current_project_state)
        self.assertEqual(gui.timeline._timeline.overlay_order, expected)
