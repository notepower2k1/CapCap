import sys
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
import unittest
for folder in (Path(__file__).resolve().parents[1], Path(__file__).resolve().parents[1] / "app", Path(__file__).resolve().parents[1] / "ui"):
    sys.path.insert(0, str(folder))
from app.output_geometry import resolve_output_dimensions, normalize_canvas


class OutputGeometryTests(unittest.TestCase):
    def test_letterbox_detection_ignores_fill_crop_and_rounding(self):
        from PySide6.QtCore import QRectF
        from ui.utils.video_geometry import has_letterbox, native_subtitle_preview
        canvas = QRectF(0, 0, 1080, 1920)
        for source, expected in ((QRectF(0, 656.25, 1080, 607.5), True),
                                 (canvas, False), (QRectF(-100, 0, 1280, 1920), False),
                                 (QRectF(.1, .1, 1079.8, 1919.8), False)):
            view = SimpleNamespace(get_preview_canvas_rect=lambda: canvas,
                                   get_video_content_rect=lambda: source, limit_layers_to_video=False)
            self.assertEqual(has_letterbox(view), expected)
            before = native_subtitle_preview(view)
            view.limit_layers_to_video = True
            if not expected:
                self.assertEqual(native_subtitle_preview(view), before)
    def test_legacy_layout_keys_restore_sizes_after_bounds_option_upgrade(self):
        from ui.utils.layer_framing import lookup_layout
        import json
        expected = dict(font_size=67, x=.8)
        for old in (("fit", 1920, 1080), ("16:9", "fit", 1920, 1080)):
            self.assertEqual(lookup_layout({json.dumps(old): expected}, json.dumps(("fit", 1920, 1080, False))), expected)

    def test_selected_subtitle_can_be_edited_past_frame_when_limit_is_off(self):
        from PySide6.QtWidgets import QWidget
        from ui.widgets.mpv_video_view import MpvVideoView
        from PySide6.QtGui import QColor
        host = QWidget()
        host.resize(800, 600)
        view = MpvVideoView(host, native_surface=False)
        view.setGeometry(0, 0, 700, 500)
        try:
            host.show()
            view.show()
            view.set_video_dimensions(1920, 1080)
            item = view.subtitle_item
            item.set_text("Subtitle at the frame edge")
            item.set_style("Arial", 24, QColor("white"))
            item.set_positioning(custom_position_enabled=True, custom_x_percent=50, custom_y_percent=100)
            item.set_editable(True)
            with patch("ui.widgets.mpv_video_view._should_suppress_overlay", return_value=False):
                view.limit_layers_to_video = False
                view.reposition_subtitle()
                position = view.mapFromGlobal(item.pos()) if item.is_top_level_overlay() else item.pos()
                self.assertGreater(position.y() + item.height(), view.get_preview_canvas_rect().bottom())
                view.limit_layers_to_video = True
                view.reposition_subtitle()
                position = view.mapFromGlobal(item.pos()) if item.is_top_level_overlay() else item.pos()
                self.assertGreater(position.y() + item.height(), view.get_preview_canvas_rect().bottom())
        finally:
            view.close()
            host.close()
    def test_layer_bounds_option_allows_subtitles_on_black_bars(self):
        from PySide6.QtCore import QRectF
        from ui.utils.video_geometry import layer_bounds_rect
        from app.subtitle_presentation import subtitle_to_canvas
        canvas, video = QRectF(0, 0, 1080, 1920), QRectF(0, 656.25, 1080, 607.5)
        view = SimpleNamespace(get_preview_canvas_rect=lambda: canvas, get_video_content_rect=lambda: video,
                               limit_layers_to_video=False)
        self.assertEqual(layer_bounds_rect(view), canvas)
        style = dict(custom_position_enabled=True, custom_position_x=50, custom_position_y=90, font_size=30)
        full = subtitle_to_canvas(style, (1920, 1080), (1080, 1920))
        self.assertEqual(full["custom_position_y"], 90)
        self.assertGreater(full["custom_position_y"] / 100 * 1920, video.bottom())
        view.limit_layers_to_video = True
        self.assertEqual(layer_bounds_rect(view), canvas)
        limited = subtitle_to_canvas(dict(style, limit_layers_to_video=True), (1920, 1080), (1080, 1920))
        self.assertEqual(limited["custom_position_y"], 90)
    def test_subtitle_output_font_size_survives_fit_fill_transform(self):
        from app.subtitle_presentation import canvas_to_source
        for mode in ("fit", "fill"):
            for canvas in ((1080, 1920), (1080, 1080), (1280, 720)):
                scale = (min if mode == "fit" else max)(canvas[0] / 1920, canvas[1] / 1080)
                style = canvas_to_source(dict(font_size=61), (1920, 1080), canvas, mode)
                self.assertAlmostEqual(style["font_size"] * scale, 61, delta=scale / 2 + .01)
    def test_text_drag_and_subtitle_export_use_video_region_excluding_black_bars(self):
        from PySide6.QtCore import QRectF
        from ui.utils.video_geometry import source_to_canvas, canvas_to_source_point, visible_video_rect
        from app.subtitle_presentation import canvas_to_source
        canvas = QRectF(0, 0, 1080, 1920)
        video = QRectF(0, 656.25, 1080, 607.5)
        view = SimpleNamespace(get_preview_canvas_rect=lambda: canvas, get_video_content_rect=lambda: video)
        x, y = source_to_canvas(view, .5, .86)
        self.assertTrue(video.contains(x * canvas.width(), y * canvas.height()))
        restored = canvas_to_source_point(view, x, y)
        self.assertAlmostEqual(restored[0], .5)
        self.assertAlmostEqual(restored[1], .86)
        self.assertEqual(visible_video_rect(view), video)
        style = canvas_to_source(dict(custom_position_enabled=True, custom_position_x=50,
                                     custom_position_y=86, font_size=30), (1920, 1080), (1080, 1920))
        self.assertAlmostEqual(style["custom_position_x"], 50)
        self.assertAlmostEqual(style["custom_position_y"], 86)
        self.assertEqual(style["font_size"], round(30 / .5625))
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.application = QApplication.instance() or QApplication([])

    def test_canvas_roundtrip_restores_exact_text_and_logo_layout_after_serialization(self):
        from PySide6.QtCore import QRectF
        from app.layers.text import TextLayer
        from app.layers.image import ImageLayer
        from app.layers.transform import Transform
        from ui.utils.layer_framing import reframe_layers
        import json
        text = TextLayer(text="Original caption", font_size=67, transform=Transform(x=.87, y=.9))
        logo = ImageLayer(transform=Transform(x=83., y=5., scale_x=1.2, scale_y=.7))
        expected = [(layer.transform.to_dict(), getattr(layer, "font_size", None)) for layer in (text, logo)]
        old = QRectF(0, 0, 1920, 1080)
        source = QRectF(-656.25, 0, 1920, 1080)
        visible = QRectF(0, 0, 607.5, 1080)
        track = SimpleNamespace(locked=False, layers=[text, logo])
        reframe_layers([track], old, old, source, visible, font_scale=.316,
                       old_key="landscape", new_key="portrait")
        track.layers = [type(layer).from_dict(json.loads(json.dumps(layer.to_dict()))) for layer in track.layers]
        for _ in range(3):
            reframe_layers([track], source, visible, old, old, font_scale=.316,
                           old_key="portrait", new_key="landscape")
            self.assertEqual([(layer.transform.to_dict(), getattr(layer, "font_size", None)) for layer in track.layers], expected)
            reframe_layers([track], old, old, source, visible, font_scale=.316,
                           old_key="landscape", new_key="portrait")

    def test_reframing_landscape_to_portrait_keeps_text_and_logo_visible(self):
        from PySide6.QtCore import QRectF
        from app.layers.text import TextLayer
        from app.layers.image import ImageLayer
        from app.layers.transform import Transform
        from ui.utils.layer_framing import reframe_layers
        text = TextLayer(transform=Transform(x=.9, y=.85))
        logo = ImageLayer(transform=Transform(x=80., y=10., scale_x=1., scale_y=1.))
        track = SimpleNamespace(locked=False, layers=[text, logo])
        old = QRectF(0, 0, 1920, 1080)
        source = QRectF(-656.25, 0, 1920, 1080)
        visible = QRectF(0, 0, 607.5, 1080)
        reframe_layers([track], old, old, source, visible)
        self.assertTrue(visible.contains(source.left() + text.transform.x * source.width(),
                                         source.top() + text.transform.y * source.height()))
        rect = QRectF(source.left() + logo.transform.x / 100 * source.width(),
                      source.top() + logo.transform.y / 100 * source.height(),
                      .2 * logo.transform.scale_x * source.width(),
                      .2 * logo.transform.scale_y * source.height())
        self.assertTrue(visible.adjusted(-.01, -.01, .01, .01).contains(rect))
        self.assertAlmostEqual(rect.width() / rect.height(), 1920 / 1080)

    def test_reframe_scales_text_and_preserves_locked_layers(self):
        from PySide6.QtCore import QRectF
        from app.layers.text import TextLayer
        from app.layers.transform import Transform
        from ui.utils.layer_framing import reframe_layers
        text = TextLayer(text="A long caption for the new narrow canvas", font_size=100,
                         transform=Transform(x=.9, y=.9))
        locked = TextLayer(font_size=100, locked=True, transform=Transform(x=.9, y=.9))
        old = QRectF(0, 0, 1920, 1080)
        source = QRectF(-656.25, 0, 1920, 1080)
        visible = QRectF(0, 0, 607.5, 1080)
        reframe_layers([SimpleNamespace(locked=False, layers=[text, locked])], old, old, source, visible,
                       font_scale=.316, pixel_scale=1.)
        self.assertLess(text.font_size, 100)
        self.assertEqual(locked.font_size, 100)
        self.assertEqual(locked.transform.x, .9)

    def test_source_ratio_is_preserved_at_lower_quality(self):
        self.assertEqual(resolve_output_dimensions(2400, 1800, "720p"), (960, 720))
        self.assertEqual(resolve_output_dimensions(1800, 2400, "1080p"), (1080, 1440))
        self.assertEqual(resolve_output_dimensions(2000, 2000, "1080p"), (1080, 1080))
        self.assertEqual(resolve_output_dimensions(640, 480, "1080p"), (None, None))

    def test_custom_ratio_and_exact_size_have_distinct_quality_rules(self):
        self.assertEqual(resolve_output_dimensions(3840, 2160, "720p", "3:2"), (1080, 720))
        self.assertEqual(resolve_output_dimensions(3840, 2160, "source", "3:2"), (3240, 2160))
        self.assertEqual(resolve_output_dimensions(640, 480, "720p", "size:1200x1600"), (1200, 1600))
        self.assertEqual(normalize_canvas("1200 × 1600"), "size:1200x1600")
        for value in ("0:1", "NaN:2", "1:0", "1:1000", "1921x1080", "20000x20000"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_canvas(value)

    def test_preview_and_export_agree_for_source_custom_ratio_and_size(self):
        from ui.controllers.preview_controller import PreviewController
        from app.workflows.export_workflow import ExportWorkflow
        for canvas in ("source", "3:2", "size:1080x1920", "9:16"):
            gui = SimpleNamespace(get_output_ratio_key=lambda: canvas, get_output_quality_key=lambda: "720p")
            preview = PreviewController.__new__(PreviewController)
            preview.gui = gui
            export = ExportWorkflow.__new__(ExportWorkflow)
            export.engine_runtime = MagicMock()
            export.engine_runtime.get_video_dimensions.return_value = (2400, 1800)
            with patch("video_processor.get_video_dimensions", return_value=(2400, 1800)):
                self.assertEqual(preview._resolve_output_canvas_dimensions("source.mp4"),
                                 export._resolve_target_dimensions("source.mp4", "720p", canvas))

    def test_custom_canvas_restores_into_project_controls(self):
        from PySide6.QtWidgets import QApplication, QComboBox
        from ui.utils.performance_config import restore
        app = QApplication.instance() or QApplication([])
        gui = SimpleNamespace(output_ratio_combo=QComboBox())
        gui.output_ratio_combo.addItem("Source", "source")
        restore(gui, dict(controls={"output_ratio_combo": dict(data="size:1200x1600", text="1200x1600")}), refresh=False)
        self.assertEqual(gui.output_ratio_combo.currentData(), "size:1200x1600")

    def test_all_preview_backends_resolve_custom_canvas_identically(self):
        from ui.widgets.video_view import VideoView
        from ui.widgets.mpv_video_view import MpvVideoView
        from ui.views.preview_panel import FramePreviewWidget
        host = SimpleNamespace(video_source_width=2400, video_source_height=1800)
        for key, expected in (("3:2", 1.5), ("size:1200x1600", .75), ("source", 4/3)):
            host.preview_aspect_key = key
            for cls in (VideoView, MpvVideoView, FramePreviewWidget):
                self.assertAlmostEqual(cls._resolve_canvas_aspect_ratio(host), expected)
