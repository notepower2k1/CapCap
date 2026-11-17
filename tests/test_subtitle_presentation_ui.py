"""Exercise the real editor's bilingual controls and project isolation."""
import copy
import json
import time
import unittest
from unittest.mock import patch
import test_performance_queue as harness
from PySide6.QtCore import QCoreApplication
from app.subtitle_presentation import KEY


class SubtitlePresentationUiTests(unittest.TestCase):
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

    def editor(self):
        gui = self.host.editor(self.host.source())
        gui.current_segments = [{"start": 0., "end": .8, "text": "Original text"}]
        gui.current_translated_segments = [{"start": 0., "end": .8, "text": "Main text"}]
        gui.apply_segments_to_timeline()
        return gui

    def test_ratio_change_alone_places_text_and_logo_on_letterbox_canvas(self):
        from pathlib import Path
        from app.layers.text import TextLayer
        from app.layers.image import ImageLayer
        from app.layers.transform import Transform
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        from ui.utils.video_geometry import source_to_canvas
        gui = self.editor()
        with patch("video_processor.get_video_dimensions", return_value=(1920, 1080)):
            gui.video_view.video_source_width, gui.video_view.video_source_height = 1920, 1080
            gui.output_quality_combo.setCurrentIndex(gui.output_quality_combo.findData("source"))
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
            gui._sync_preview_output_canvas_dimensions()
            text = TextLayer(text="Bottom title", font_size=40, start=0, end=.8, transform=Transform(x=.5, y=.85))
            logo = ImageLayer(source=str(Path(__file__).resolve().parents[1] / "assets" / "capcap.png"), start=0, end=.8, transform=Transform(x=20, y=5, scale_x=.5, scale_y=.5))
            # Earlier builds cached source-relative positions under this key.
            import json
            legacy_key = json.dumps(["fit", 606, 1080, False])
            for layer in (text, logo):
                saved = {key: getattr(layer.transform, key) for key in ("x", "y", "scale_x", "scale_y")}
                if layer is text:
                    saved["font_size"] = layer.font_size
                layer.metadata["_canvas_layouts"] = {legacy_key: saved}
            find_or_create_track(gui.timeline._timeline, "T1 Text", LayerType.TEXT, 80).layers.append(text)
            find_or_create_track(gui.timeline._timeline, "L1 Logo", LayerType.IMAGE, 80).layers.append(logo)
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("9:16"))
            QCoreApplication.processEvents()
            x, y = source_to_canvas(gui.video_view, text.transform.x, text.transform.y)
            self.assertAlmostEqual(y, .85, delta=.01)
            canvas = gui.video_view.get_preview_canvas_rect()
            source = gui.video_view.get_video_content_rect()
            self.assertGreater(canvas.top() + y * canvas.height(), source.bottom())
            logo_rect = gui.video_view.logo_overlay.region_rect(0)
            self.assertLess(logo_rect.bottom(), source.top())
            # New whole-canvas manual edits must still restore exactly.
            from ui.utils.video_geometry import canvas_to_source_point
            text.font_size = 37
            text.transform.x, text.transform.y = canvas_to_source_point(gui.video_view, .25, .92)
            saved = copy.deepcopy(text.transform.to_dict())
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
            QCoreApplication.processEvents()
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("9:16"))
            QCoreApplication.processEvents()
            self.assertEqual(text.font_size, 37)
            self.assertEqual(text.transform.to_dict(), saved)

    def test_fast_preview_accepts_none_worker_and_builds_canvas_sized_subtitles(self):
        from unittest.mock import MagicMock
        from pathlib import Path
        from app.layers.text import TextLayer
        from app.layers.transform import Transform
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        gui = self.editor()
        controller = gui.preview_controller
        text = TextLayer(text="Fast preview text", font_size=60, start=0, end=.8, transform=Transform(x=.6, y=.8))
        find_or_create_track(gui.timeline._timeline, "T1 Text", LayerType.TEXT, 80).layers.append(text)
        gui.quick_preview_thread = None
        worker = MagicMock()
        module = controller.__class__.__module__
        with patch(module + ".QFileDialog.getExistingDirectory", return_value=str(self.host.root)), \
                patch(module + ".QuickPreviewWorker", return_value=worker) as factory, \
                patch.object(gui, "get_output_mode_key", return_value="subtitle"), \
                patch.object(gui, "get_output_scale_mode_key", return_value="fit"), \
                patch.object(gui, "get_output_ratio_key", return_value="9:16"), \
                patch.object(gui, "get_output_quality_key", return_value="1080p"), \
                patch.object(gui, "_subtitle_render_dimensions", return_value=(1080, 1920)), \
                patch("video_processor.get_video_dimensions", return_value=(1920, 1080)):
            controller.preview_five_seconds()
        worker.start.assert_called_once()
        options = factory.call_args.kwargs
        script = Path(options["ass_path"]).read_text(encoding="utf-8-sig")
        self.assertIn("PlayResX: 1080", script)
        self.assertIn("PlayResY: 1920", script)
        font_sizes = [float(line.split(",")[2]) for line in script.splitlines() if line.startswith("Style:")]
        self.assertIn(float(gui.get_subtitle_export_style()["font_size"]), font_sizes)
        self.assertTrue(options["text_image_layers"])
        # Fit puts source y=.8 inside the picture, not at canvas y=.8.
        self.assertLess(options["text_image_layers"][0]["y"], 1300)
        from PySide6.QtGui import QImage
        image = QImage(options["text_image_layers"][0]["path"])
        self.assertAlmostEqual(options["text_image_layers"][0]["y"] + image.height() / 2,
                               656.25 + .8 * 607.5, delta=1.5)
        self.assertEqual(options["target_width"], 1080)
        self.assertEqual(options["target_height"], 1920)
        gui.quick_preview_thread = None

    def test_text_font_percentage_is_flexible_and_independent_per_layer(self):
        from app.layers.text import TextLayer
        from app.layers.base import LayerType
        from app.layers.sync_bridge import find_or_create_track
        gui = self.editor()
        track = find_or_create_track(gui.timeline._timeline, "T1 Text", LayerType.TEXT, 80)
        first = TextLayer(text="First", font_size=61, start=0, end=.8)
        second = TextLayer(text="Second", font_size=18, start=0, end=.8)
        track.layers.extend([first, second])
        subtitles = copy.deepcopy(gui.subtitle_presentation_controller.primary_controls())
        gui.timeline.select_layer(first.id)
        gui.on_timeline_layer_selected(first.id)
        self.assertEqual(gui.text_inspector_size_spin.value(), 101.67)
        self.assertTrue(gui.text_inspector_font_combo.isEditable())
        gui.text_inspector_size_spin.setValue(225)
        self.assertEqual(first.font_size, 135)
        self.assertEqual(second.font_size, 18)
        gui.timeline.select_layer(second.id)
        gui.on_timeline_layer_selected(second.id)
        self.assertEqual(gui.text_inspector_size_spin.value(), 30.)
        gui.text_inspector_size_spin.setValue(37.3)
        self.assertEqual(second.font_size, 22)
        self.assertEqual(gui.text_inspector_size_spin.value(), 36.67)
        gui.text_inspector_font_combo.setCurrentText("Tahoma")
        self.assertEqual(second.font_name, "Tahoma")
        self.assertEqual(first.font_name, "Arial")
        gui.timeline.select_layer(first.id)
        gui.on_timeline_layer_selected(first.id)
        self.assertEqual(gui.text_inspector_size_spin.value(), 225.)
        first.font_size = 19  # Auto-reframe also updates the displayed value.
        gui._refresh_text_layer_preview(first.id)
        self.assertEqual(gui.text_inspector_size_spin.value(), 31.67)
        self.assertEqual(TextLayer.from_dict(second.to_dict()).font_size, 22)
        self.assertEqual(gui.subtitle_presentation_controller.primary_controls(), subtitles)


    def test_all_layer_sizes_roundtrip_with_default_canvas_options(self):
        from app.layers.text import TextLayer
        from app.layers.image import ImageLayer
        from app.layers.transform import Transform
        from app.layers.sync_bridge import find_or_create_track
        from app.layers.base import LayerType
        gui = self.editor()
        text = TextLayer(text="A test caption", font_size=67, start=0, end=.8, transform=Transform(x=.8, y=.8))
        logo = ImageLayer(source=str(self.host.source()), start=0, end=.8, transform=Transform(x=75, y=15))
        find_or_create_track(gui.timeline._timeline, "T1 Text", LayerType.TEXT, 80).layers.append(text)
        find_or_create_track(gui.timeline._timeline, "L1 Logo", LayerType.IMAGE, 80).layers.append(logo)
        with patch("video_processor.get_video_dimensions", return_value=(1080, 1920)):
            gui.video_view.video_source_width, gui.video_view.video_source_height = 1080, 1920
            gui.output_quality_combo.setCurrentIndex(gui.output_quality_combo.findData("source"))
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
            gui._sync_preview_output_canvas_dimensions()
            original = (text.font_size, copy.deepcopy(text.transform.to_dict()), copy.deepcopy(logo.transform.to_dict()),
                        gui.get_subtitle_export_style()["font_size"])
            for _ in range(3):
                gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("9:16"))
                QCoreApplication.processEvents()
                authored = gui._subtitle_render_dimensions()
                gui.video_view.subtitle_render_width, gui.video_view.subtitle_render_height = 320, 180
                self.assertEqual(gui._capture_layer_framing()[2], authored)
                gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
                QCoreApplication.processEvents()
                self.assertEqual((text.font_size, text.transform.to_dict(), logo.transform.to_dict(),
                                  gui.get_subtitle_export_style()["font_size"]), original)

    def test_bilingual_canvas_preview_uses_independent_cue_animations(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.cfg["styles"]["primary"].update(animation="Pop In", animation_time=.4)
        controller.cfg["styles"]["secondary"].update(animation="Fade Out", animation_time=.4)
        controller.apply_controls(controller.cfg["styles"]["primary"])
        controller.fallback_preview(gui.get_active_segments(), 100)
        primary, secondary = controller.overlays
        self.assertEqual(primary.animation_style, "pop in")
        self.assertAlmostEqual(primary.animation_progress, .25)
        self.assertEqual(secondary.animation_style, "fade out")
        self.assertAlmostEqual(secondary.animation_opacity, 1.)
        controller.fallback_preview(gui.get_active_segments(), 700)
        self.assertAlmostEqual(primary.animation_progress, 1.)
        self.assertLess(secondary.animation_opacity, .4)

    def test_style_refresh_keeps_animation_at_current_playback_time(self):
        gui = self.editor()
        gui.subtitle_animation_combo.setCurrentText("Pop In")
        gui.subtitle_animation_time_spin.setValue(.4)
        with patch.object(gui.media_player, "position", return_value=200):
            from ui.main_window import VideoTranslatorGUI
            VideoTranslatorGUI._set_live_subtitle_effects(gui, gui.current_translated_segments[0])
        self.assertAlmostEqual(gui.video_view.subtitle_item.animation_progress, .5)

    def test_layer_options_defaults_disable_auto_reframe_and_restore_with_project(self):
        from ui.utils.performance_config import restore
        gui = self.editor()
        self.assertTrue(gui.auto_layer_reframe_cb.isChecked())
        self.assertFalse(hasattr(gui, "limit_layers_to_video_cb"))
        gui.auto_layer_reframe_cb.setChecked(False)
        before = copy.deepcopy(gui.subtitle_presentation_controller.cfg["styles"])
        with patch.object(gui, "_subtitle_render_dimensions", return_value=(1080, 1920)):
            gui._restore_layer_framing((None, None, (1920, 1080)))
        self.assertEqual(gui.subtitle_presentation_controller.cfg["styles"], before)
        restore(gui, dict(controls={"auto_layer_reframe_cb": False, "limit_layers_to_video_cb": True}), refresh=False)
        self.assertNotIn("limit_layers_to_video", gui.get_subtitle_export_style())

    def test_subtitle_percentage_accepts_custom_values_and_reports_actual_pixel_size(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        gui.subtitle_font_size_spin.setValue(18)
        self.assertEqual(gui.subtitle_font_scale_spin.value(), 30.)
        gui.subtitle_font_scale_spin.setValue(200)
        self.assertEqual(gui.subtitle_font_size_spin.value(), 120)
        gui.subtitle_font_size_spin.setValue(61)
        self.assertEqual(gui.subtitle_font_scale_spin.value(), 101.67)
        self.assertEqual(gui.get_subtitle_export_style()["font_size"], 61)
        controller.capture_style()
        controller.style_role.setCurrentIndex(1)
        gui.subtitle_font_scale_spin.setValue(35)
        self.assertEqual(gui.subtitle_font_size_spin.value(), 21)
        controller.style_role.setCurrentIndex(0)
        self.assertEqual(gui.subtitle_font_scale_spin.value(), 101.67)
        self.assertEqual(gui.subtitle_font_size_spin.value(), 61)

    def test_ratio_change_scales_both_subtitle_styles_without_overwriting_defaults(self):
        from PySide6.QtCore import QRectF
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.capture_style()
        before = {role: style["size"] for role, style in controller.cfg["styles"].items()}
        defaults = gui.settings.value("subtitle_style_default_primary")
        old = QRectF(0, 0, 1920, 1080)
        canvas = QRectF(0, 0, 608, 1080)
        source = QRectF(-656, 0, 1920, 1080)
        with patch.object(gui, "_subtitle_render_dimensions", return_value=(608, 1080)), \
                patch.object(gui.video_view, "get_video_content_rect", return_value=source), \
                patch.object(gui.video_view, "get_preview_canvas_rect", return_value=canvas):
            gui._restore_layer_framing((old, old, (1920, 1080)))
        for role in before:
            self.assertAlmostEqual(controller.cfg["styles"][role]["size"], round(before[role] * 608 / 1920), delta=1)
        self.assertEqual(gui.settings.value("subtitle_style_default_primary"), defaults)

    def test_subtitle_size_restores_exactly_when_returning_to_original_canvas(self):
        from PySide6.QtCore import QRectF
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.capture_style()
        before = copy.deepcopy(controller.cfg["styles"])
        old = QRectF(0, 0, 1920, 1080)
        portrait = QRectF(0, 0, 608, 1080)
        source = QRectF(-656, 0, 1920, 1080)
        for _ in range(3):
            gui.video_view.preview_aspect_key = "9:16"
            gui.video_view.preview_scale_mode = "fit"
            with patch.object(gui, "_subtitle_render_dimensions", return_value=(608, 1080)), \
                    patch.object(gui.video_view, "get_video_content_rect", return_value=source), \
                    patch.object(gui.video_view, "get_preview_canvas_rect", return_value=portrait):
                gui._restore_layer_framing((old, old, (1920, 1080), ("16:9", "fit", 1920, 1080)))
            self.assertLess(controller.export_style(role="primary")["font_size"], before["primary"]["size"])
            gui.video_view.preview_aspect_key = "16:9"
            with patch.object(gui, "_subtitle_render_dimensions", return_value=(1920, 1080)), \
                    patch.object(gui.video_view, "get_video_content_rect", return_value=old), \
                    patch.object(gui.video_view, "get_preview_canvas_rect", return_value=old):
                gui._restore_layer_framing((source, portrait, (608, 1080), ("9:16", "fit", 608, 1080)))
            self.assertEqual(controller.cfg["styles"], before)

    def test_ratio_combo_updates_exported_subtitle_size_and_restores_it(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        with patch("video_processor.get_video_dimensions", return_value=(1920, 1080)):
            gui.video_view.video_source_width = 1920
            gui.video_view.video_source_height = 1080
            gui.output_quality_combo.setCurrentIndex(gui.output_quality_combo.findData("source"))
            gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
            gui._sync_preview_output_canvas_dimensions()
            original = controller.export_style(role="primary")["font_size"]
            for _ in range(3):
                gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("9:16"))
                QCoreApplication.processEvents()
                self.assertLess(controller.export_style(role="primary")["font_size"], original)
                gui.output_ratio_combo.setCurrentIndex(gui.output_ratio_combo.findData("16:9"))
                QCoreApplication.processEvents()
                self.assertEqual(controller.export_style(role="primary")["font_size"], original)

    def test_bilingual_playback_follows_main_inspector_without_rebuilding_or_saving(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        gui.current_segments.append(dict(start=1., end=2., text="Original two"))
        gui.current_translated_segments.append(dict(start=1., end=2., text="Main two"))
        gui.apply_segments_to_timeline()
        controller.mode.setCurrentIndex(controller.mode.findData("bilingual"))
        gui.set_selected_segment_index(0)
        gui._switch_inspector("subtitle")
        with patch.object(gui, "_preview_is_playing", return_value=True), \
                patch.object(gui, "persist_current_timeline_project_data") as save, \
                patch.object(gui, "apply_segments_to_timeline") as rebuild:
            gui.update_playback_subtitle_highlight(1500)
            self.assertEqual(gui._selected_segment_index, 1)
            self.assertEqual(gui.timeline.segment_index_for_layer_id(gui.timeline._selected_layer_id), 1)
            with patch.object(gui, "set_selected_segment_index") as select:
                gui.update_playback_subtitle_highlight(1600)
                select.assert_not_called()
            save.assert_not_called()
            rebuild.assert_not_called()
        with patch.object(gui, "_preview_is_playing", return_value=False):
            gui.update_playback_subtitle_highlight(100)
        self.assertEqual(gui._selected_segment_index, 1)

    def test_bilingual_playback_follows_secondary_own_timing_and_keeps_paused_selection(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(controller.mode.findData("bilingual"))
        controller.replace_timeline_rows([dict(start=0., end=.3, text="Secondary one"),
                                          dict(start=.4, end=.7, text="Secondary two")])
        controller.select_secondary(0)
        main = copy.deepcopy(gui.current_translated_segments)
        with patch.object(gui, "_preview_is_playing", return_value=True), \
                patch.object(gui, "persist_current_timeline_project_data") as save, \
                patch.object(gui, "apply_segments_to_timeline") as rebuild:
            gui.update_playback_subtitle_highlight(500)
            self.assertEqual(controller.selected_secondary_index, 1)
            self.assertEqual(controller.inspector.editor.toPlainText(), "Secondary two")
            self.assertEqual(gui.timeline._selected_layer_id, "secondary-1")
            with patch.object(controller, "select_secondary") as select:
                gui.update_playback_subtitle_highlight(600)
                gui.update_playback_subtitle_highlight(900)  # Gap: retain last inspected cue.
                select.assert_not_called()
            save.assert_not_called()
            rebuild.assert_not_called()
        with patch.object(gui, "_preview_is_playing", return_value=False):
            gui.update_playback_subtitle_highlight(100)
        self.assertEqual(controller.selected_secondary_index, 1)
        self.assertEqual(gui.current_translated_segments, main)

    def test_modes_keep_main_and_voice_data_and_add_independent_secondary_track(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        main = copy.deepcopy(gui.current_translated_segments)
        controller.mode.setCurrentIndex(controller.mode.findData("bilingual"))
        tracks = gui.timeline._timeline.tracks
        track = next(t for t in tracks if t.name == "TS2")
        self.assertEqual(track.layers[0].text, "Original text")
        self.assertEqual(gui.current_translated_segments, main)
        controller.mode.setCurrentIndex(controller.mode.findData("none"))
        self.assertFalse(track.visible)
        self.assertEqual(gui.current_translated_segments, main)
        self.assertEqual(gui.get_subtitle_export_style()[KEY]["mode"], "none")
        controller.mode.setCurrentIndex(controller.mode.findData("bilingual"))
        self.assertTrue(track.visible)

    def test_style_banks_do_not_leak_into_main_export_and_project_restores_them(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        gui.subtitle_font_size_spin.setValue(64)
        controller.commit_style()
        controller.style_role.setCurrentIndex(1)
        gui.subtitle_font_size_spin.setValue(38)
        controller.commit_style()
        self.assertEqual(gui.get_subtitle_export_style()["font_size"], 64)
        self.assertEqual(gui.get_subtitle_export_style()[KEY]["secondary_render_style"]["font_size"], 38)
        state = gui.current_project_state
        saved = copy.deepcopy(state.settings[KEY])
        controller.restore(state)
        self.assertEqual(gui.subtitle_font_size_spin.value(), 64)
        self.assertEqual(controller.cfg["styles"]["secondary"]["size"], 38)
        self.assertEqual(state.settings[KEY], saved)

    def test_secondary_edit_preserves_main_and_updates_media_timing(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.select_secondary(0)
        controller.inspector.editor.setPlainText("Edited secondary")
        self.assertEqual(controller.cfg["source"], "original")
        self.assertEqual(controller.secondary_rows()[0]["text"], "Edited secondary")
        self.assertEqual(gui.current_translated_segments[0]["text"], "Main text")
        layer = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0]
        controller.move_layer(layer, .2, .9)
        self.assertEqual(controller.secondary_rows()[0]["start"], .2)

    def test_capcut_snapshot_omits_subtitle_text_but_keeps_tts_layers(self):
        gui = self.editor()
        from ui.controllers.capcut_export_controller import capture_snapshot
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        snapshot = capture_snapshot(gui)
        second = next(t for t in snapshot["timeline"]["tracks"] if t["name"] == "TS2")
        self.assertTrue(second["visible"])
        self.assertTrue(second["layers"][0]["metadata"]["secondary_subtitle"])
        controller.mode.setCurrentIndex(0)
        snapshot = capture_snapshot(gui)
        subtitle_tracks = [t for t in snapshot["timeline"]["tracks"] if t["type"] in {"subtitle", "dub_subtitle"}]
        self.assertTrue(subtitle_tracks)
        self.assertTrue(all(not t["visible"] for t in subtitle_tracks))

    def test_exported_capcut_draft_has_two_text_tracks_or_no_subtitle_text(self):
        from ui.controllers.capcut_export_controller import capture_snapshot
        from app.capcut.draft_export import DraftExporter
        from runtime_paths import bin_path
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        for mode in ("bilingual", "none"):
            controller.mode.setCurrentIndex(controller.mode.findData(mode))
            snapshot = capture_snapshot(gui)
            target = self.host.root / ("capcut-" + mode)
            DraftExporter(ffmpeg=bin_path("ffmpeg", "ffmpeg.exe"), ffprobe=bin_path("ffmpeg", "ffprobe.exe")).export(snapshot, str(target))
            draft = json.loads((target / "draft_content.json").read_text(encoding="utf-8"))
            tracks = [track for track in draft["tracks"] if track["type"] == "text" and track.get("segments")]
            self.assertEqual(len(tracks), 2 if mode == "bilingual" else 0)

    def test_queue_creates_original_secondary_and_exports_without_cloud_translation(self):
        from app.queue_worker import run_job
        from app.subtitle_presentation import KEY
        video = self.host.source()
        cfg = self.host.config(video)
        cfg["prepare"]["subtitle_presentation"] = {"mode": "bilingual", "source": "original", "language": "en"}
        cfg["export"]["subtitle_style"][KEY] = cfg["prepare"]["subtitle_presentation"]
        self.host.profile(video, cfg)
        with patch("app.subtitle_presentation.build_secondary", wraps=__import__("app.subtitle_presentation", fromlist=["build_secondary"]).build_secondary) as build:
            result = run_job(str(self.host.root), str(video), cfg)
        self.assertTrue(build.called)
        self.assertTrue(result)
        state = self.host.service.load_project(result["project"])
        from services.gui_project_bridge import GUIProjectBridge
        context = GUIProjectBridge(self.host.service).load_context(state)
        rows = context["current_translated_segments"] or context["current_segments"]
        self.assertIn("_completed_timing", rows[0])

    def test_switching_project_does_not_copy_previous_secondary_and_preserves_cached_styles(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("srt"))
        controller.cfg["secondary_segments"] = [{"start": 0., "end": .8, "text": "Private second track"}]
        controller.persist()
        first_state = gui.current_project_state
        second = self.host.service.ensure_project(str(self.host.source("second.mp4")))
        controller.restore(second)
        self.assertEqual(controller.cfg["mode"], "single")
        self.assertEqual(controller.cfg["secondary_segments"], [])
        controller.restore(first_state)
        self.assertEqual(controller.cfg["secondary_segments"][0]["text"], "Private second track")

    def test_fast_preview_none_never_falls_back_to_main_srt(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(0)
        ass = gui.preview_controller._build_fast_preview_subtitle_ass(0, .8)
        self.assertTrue(ass)
        from pathlib import Path
        self.assertNotIn("Dialogue:", Path(ass).read_text(encoding="utf-8-sig"))

    def test_style_cache_is_independent_and_saves_primary_processing_profile_while_editing_secondary(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        gui.subtitle_font_size_spin.setValue(66)
        controller.commit_style()
        controller.style_role.setCurrentIndex(1)
        gui.subtitle_font_size_spin.setValue(39)
        controller.commit_style()
        gui.save_processing_profile()
        profile = gui.current_project_state.settings["processing_profile"]
        self.assertEqual(profile["export"]["subtitle_style"]["font_size"], 66)
        self.assertEqual(profile["export"]["subtitle_style"][KEY]["secondary_render_style"]["font_size"], 39)
        self.assertEqual(controller.role, "secondary")
        self.assertEqual(gui.subtitle_font_size_spin.value(), 39)
        other = self.host.service.ensure_project(str(self.host.source("cache-new.mp4")))
        controller.restore(other)
        self.assertEqual(controller.cfg["styles"]["primary"]["size"], 66)
        self.assertEqual(controller.cfg["styles"]["secondary"]["size"], 39)

    def test_manual_translation_runs_in_worker_preserves_source_timing_and_saves_srt(self):
        from unittest.mock import MagicMock
        from pathlib import Path
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("translate"))
        engine = MagicMock()
        engine.translate_segments.return_value = [{"start": 99., "end": 100., "text": "Extra translation"}]
        with patch("services.engine_runtime.EngineRuntime", return_value=engine), \
                patch.object(controller, "review_translation", return_value={"override_provider": "google"}):
            controller.build()
            deadline = time.monotonic() + 5
            while controller.busy and time.monotonic() < deadline:
                controller.worker.wait(20)
                QCoreApplication.processEvents()
            self.assertFalse(controller.busy)
        rows = controller.secondary_rows()
        self.assertEqual([{key: value for key, value in row.items() if key != "_completed_timing"} for row in rows], [{"start": 0., "end": .8, "text": "Extra translation", "source_text": "Original text"}])
        self.assertTrue(Path(gui.current_project_state.artifacts["subtitle_secondary_srt"]).is_file())
        self.assertIsNone(controller.progress_dialog)
        self.assertEqual(engine.translate_segments.call_args.kwargs["override_provider"], "google")
        self.assertTrue(callable(engine.translate_segments.call_args.kwargs["is_canceled"]))
        gui.on_timeline_layer_selected("secondary-0")
        self.assertEqual(controller.inspector.original.text(), "Original text")
        self.assertEqual(controller.inspector.editor.toPlainText(), "Extra translation")
        controller.validate_export()
        gui.current_segments[0]["text"] = "Updated original"
        controller.validate_export()

    def test_secondary_settings_reuses_dialog_with_own_prompt_and_target(self):
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QDialog
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("translate"))
        controller.cfg.update(language="en", secondary_preset_id="general_default", secondary_prompt="Secondary custom")
        dialog = MagicMock(selected_provider="openai", selected_preset_id="general_default",
                           selected_prompt="Revised secondary custom", selected_batch_size=40,
                           selected_auto_context=True, selected_review_context=True)
        dialog.exec.return_value = QDialog.Accepted
        with patch("ui.controllers.subtitle_controller.TranslationPromptDialog", return_value=dialog) as factory, \
                patch.object(gui.subtitle_controller, "_extract_and_review_context", return_value="Confirmed secondary rules") as review:
            options = controller.review_translation()
        self.assertEqual(factory.call_args.kwargs["target_lang"], "en")
        self.assertEqual(factory.call_args.kwargs["custom_prompt"], "Secondary custom")
        self.assertIs(factory.call_args.kwargs["persist_preset"], False)
        self.assertEqual(review.call_args.kwargs["target_lang"], "en")
        self.assertEqual(controller.cfg["secondary_prompt"], "Revised secondary custom")
        self.assertEqual(options, dict(override_provider="openai", polish_batch_size=40, context_guidance="Confirmed secondary rules"))
        with patch("ui.controllers.subtitle_controller.TranslationPromptDialog", return_value=dialog):
            dialog.exec.return_value = QDialog.Rejected
            self.assertIsNone(controller.review_translation())

    def test_secondary_progress_reports_unique_cues_fallback_and_cancel_preserves_data(self):
        from threading import Event
        from unittest.mock import MagicMock
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("translate"))
        controller.replace_timeline_rows([dict(start=0., end=.8, text="Existing secondary")])
        before = copy.deepcopy(controller.secondary_rows())
        ready, release = Event(), Event()
        engine = MagicMock()

        def translate(rows, **options):
            options["status_callback"]("google-web", "AI service is temporarily overloaded or unavailable (HTTP 503).")
            options["batch_callback"](0, rows)
            options["batch_callback"](0, rows)  # Duplicate/retry must not double count.
            ready.set()
            release.wait(5)
            return [dict(start=0., end=.8, text="Late result")]

        engine.translate_segments.side_effect = translate
        with patch("services.engine_runtime.EngineRuntime", return_value=engine), \
                patch.object(controller, "review_translation", return_value={"override_provider": "openai"}), \
                patch("ui.controllers.subtitle_presentation_controller.QMessageBox.warning") as warning:
            controller.build()
            dialog = controller.progress_dialog
            try:
                self.assertTrue(ready.wait(3))
                QCoreApplication.processEvents()
                self.assertIn("1/1", dialog.labelText())
                self.assertIn("HTTP 503", dialog.labelText())
                self.assertIn("Google Translate", dialog.labelText())
                self.assertEqual(dialog.value(), 100)
                controller.cancel_build()
            finally:
                release.set()
                controller.worker.wait(5000)
                QCoreApplication.processEvents()
            self.assertFalse(controller.busy)
            self.assertIsNone(controller.progress_dialog)
            self.assertEqual(controller.secondary_rows(), before)
            warning.assert_not_called()

    def test_secondary_cancel_discards_completion_already_queued_by_worker(self):
        from unittest.mock import MagicMock
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        before = copy.deepcopy(controller.secondary_rows())
        controller.busy = True
        controller.worker = MagicMock()
        controller.worker.isRunning.return_value = False
        controller.job_identity = (gui.current_project_state.project_id, controller.cfg["source"],
            controller.cfg["language"], controller.cfg["secondary_preset_id"], controller.cfg["secondary_prompt"])
        controller.cancel_build()
        result = copy.deepcopy(controller.cfg)
        result["secondary_segments"][0]["text"] = "Discard this late completion"
        controller.built(result, "")
        self.assertEqual(controller.secondary_rows(), before)

    def test_qt_graphics_fallback_draws_two_independent_subtitle_items(self):
        from widgets.video_view import VideoView
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        old_view, old_overlays = gui.video_view, controller.overlays
        view = VideoView(gui)
        view.video_source_width, view.video_source_height = 320, 180
        view.resize(640, 360)
        gui.video_view = view
        controller.overlays = []
        try:
            controller.fallback_preview(gui.get_active_segments(), 200)
            self.assertEqual(len(controller.overlays), 2)
            self.assertEqual([item.current_text for item in controller.overlays], ["Main text", "Original text"])
        finally:
            controller.hide_overlays()
            gui.video_view = old_view
            controller.overlays = old_overlays
            view.deleteLater()

    def test_secondary_prompt_is_saved_per_project_without_changing_main_prompt(self):
        import os
        from unittest.mock import MagicMock
        from PySide6.QtWidgets import QDialog, QComboBox, QPlainTextEdit
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        main_preset = os.environ.get("CAPCAP_TRANSLATION_PRESET_ID")
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("translate"))
        prompt = "Translate to {{target_lang}} for a documentary."
        dialog = MagicMock(selected_provider="openai", selected_preset_id="general_default",
                           selected_prompt=prompt, selected_batch_size=40,
                           selected_auto_context=False, selected_review_context=False)
        dialog.exec.return_value = QDialog.Accepted
        with patch("ui.controllers.subtitle_controller.TranslationPromptDialog", return_value=dialog):
            controller.review_translation()
        state = gui.current_project_state
        self.assertEqual(state.settings[KEY]["secondary_prompt"], prompt)
        controller.restore(state)
        self.assertEqual(controller.cfg["secondary_prompt"], prompt)
        self.assertIsNone(controller.panel.findChild(QComboBox, "secondary_translation_preset"))
        self.assertIsNone(controller.panel.findChild(QPlainTextEdit, "secondary_translation_prompt"))
        with patch("ui.controllers.subtitle_controller.TranslationPromptDialog", return_value=dialog) as factory:
            controller.review_translation()
        self.assertEqual(factory.call_args.kwargs["custom_prompt"], prompt)
        self.assertEqual(os.environ.get("CAPCAP_TRANSLATION_PRESET_ID"), main_preset)

    def test_secondary_snapshot_does_not_follow_main_edits_or_selection(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        before = copy.deepcopy(controller.secondary_rows())
        secondary = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0]
        gui.on_timeline_layer_selected(secondary.id)
        main = next(t for t in gui.timeline._timeline.tracks if t.name == "TS1").layers[0]
        gui.on_timeline_layer_selected(main.id)
        gui.current_segments[0]["text"] = "Main source changed"
        gui.current_segments[0]["start"] = .1
        gui.apply_segments_to_timeline()
        self.assertEqual(controller.secondary_rows(), before)
        self.assertEqual(controller.export_settings()["secondary_segments"], before)
        from app.subtitle_presentation import build_secondary
        self.assertEqual(build_secondary(controller.cfg, gui.current_segments)["secondary_segments"], before)
        self.assertEqual(controller.role, "primary")
        gui.current_segments = []
        gui.current_translated_segments = []
        gui.apply_segments_to_timeline()
        self.assertEqual(controller.secondary_rows(), before)
        self.assertEqual(next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0].text, "Original text")

    def test_secondary_mouse_selection_and_resize_never_emit_main_segment_signals(self):
        from PySide6.QtCore import Qt, QPointF, QEvent
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtTest import QSignalSpy
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        timeline = gui.timeline
        main = copy.deepcopy(gui.get_active_segments())
        source = copy.deepcopy(gui.current_segments)
        previous_page = gui.left_panel_stack.currentIndex()
        selected = QSignalSpy(timeline.segmentSelected)
        resized = QSignalSpy(timeline.segmentTimingChanged)
        started = QSignalSpy(timeline.segmentTimingEditStarted)
        position = QPointF(timeline.CONTENT_LEFT_PAD + 50, timeline.RULER_HEIGHT + 80)
        press = QMouseEvent(QEvent.MouseButtonPress, position, position, Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        release = QMouseEvent(QEvent.MouseButtonRelease, position, position, Qt.LeftButton, Qt.NoButton, Qt.NoModifier)
        with patch.object(timeline, "_hit_test_edge", return_value=("body", "secondary-0")):
            timeline.mousePressEvent(press)
            track, layer = timeline._find_layer_by_id("secondary-0")
            layer.start, layer.end = .2, .9
            timeline.mouseReleaseEvent(release)
        self.assertEqual(selected.count(), 0)
        self.assertEqual(resized.count(), 0)
        self.assertEqual(started.count(), 0)
        self.assertEqual(timeline.segment_index_for_layer_id("secondary-0"), -1)
        self.assertNotIn("secondary-0", timeline._segment_indices)
        self.assertEqual(gui.get_active_segments(), main)
        self.assertEqual(gui.current_segments, source)
        self.assertEqual(controller.timeline_rows()[0]["start"], .2)
        self.assertIs(gui.inspector_stack.currentWidget(), controller.inspector_scroll)
        self.assertEqual(gui.left_panel_stack.currentIndex(), previous_page)

    def test_secondary_inspector_edits_and_highlights_use_only_secondary_data(self):
        from PySide6.QtGui import QTextCursor
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        main = copy.deepcopy(gui.get_active_segments())
        source = copy.deepcopy(gui.current_segments)
        gui.on_timeline_layer_selected("secondary-0")
        inspector = controller.inspector
        self.assertEqual(inspector.editor.toPlainText(), "Original text")
        inspector.editor.setPlainText("Secondary changed")
        cursor = inspector.editor.textCursor()
        cursor.select(QTextCursor.Document)
        inspector.editor.setTextCursor(cursor)
        inspector.fields["highlight_button"].click()
        self.assertEqual(controller.secondary_rows()[0]["manual_highlights"], ["Secondary changed"])
        controller.update_secondary(0, start=.1, end=.7)
        self.assertEqual(inspector.summary.text(), "#1 / 1")
        self.assertEqual(inspector.duration.objectName(), "timingChip")
        self.assertIn("0.600s", inspector.duration.text())
        self.assertIn(gui.format_timestamp(.1), inspector.start_badge.text())
        self.assertFalse(hasattr(inspector, "previous"))
        self.assertFalse(hasattr(inspector, "next"))
        self.assertEqual(controller.timeline_rows()[0]["start"], .1)
        self.assertEqual(controller.timeline_rows()[0]["end"], .7)
        self.assertEqual(gui.get_active_segments(), main)
        self.assertEqual(gui.current_segments, source)
        self.assertEqual(gui.current_project_state.settings[KEY]["secondary_segments"][0]["text"], "Secondary changed")
        self.assertEqual(next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0].name, "Secondary changed")
        controller.restore(gui.current_project_state)
        self.assertEqual(controller.secondary_rows()[0]["manual_highlights"], ["Secondary changed"])
        controller.select_secondary(0)
        inspector.remove_highlight("Secondary changed")
        self.assertEqual(controller.secondary_rows()[0].get("manual_highlights", []), [])

    def test_secondary_offers_all_main_presets_and_keeps_choice_when_language_changes(self):
        from app.translation.prompt_loader import load_translation_presets
        from ui.controllers.subtitle_controller import TranslationPromptDialog
        from PySide6.QtWidgets import QDialog
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        controller.source.setCurrentIndex(controller.source.findData("translate"))
        def accept(dialog):
            self.assertEqual({dialog.preset_combo.itemData(i) for i in range(dialog.preset_combo.count())},
                             {preset["id"] for preset in load_translation_presets()})
            dialog.preset_combo.setCurrentIndex(dialog.preset_combo.findData("en_movie_casual"))
            dialog.auto_context_cb.setChecked(False)
            dialog._on_translate()
            return QDialog.Accepted
        with patch.object(TranslationPromptDialog, "exec", new=accept):
            controller.review_translation()
        controller.language.setCurrentIndex(controller.language.findData("vi"))
        self.assertEqual(controller.cfg["secondary_preset_id"], "en_movie_casual")
        controller.restore(gui.current_project_state)
        self.assertEqual(controller.cfg["secondary_preset_id"], "en_movie_casual")

    def test_secondary_split_delete_and_undo_never_change_main(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        before = copy.deepcopy(gui.get_active_segments())
        gui.timeline.select_layer("secondary-0")
        with patch.object(gui.media_player, "position", return_value=400):
            gui.split_selected_timeline_segment()
        self.assertEqual(len(controller.secondary_rows()), 2)
        gui.delete_selected_timeline_segment()
        self.assertEqual(len(controller.secondary_rows()), 1)
        controller.cfg["secondary_prompt"] = "Keep settings when undoing a timeline edit"
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(len(controller.secondary_rows()), 2)
        self.assertEqual(controller.cfg["secondary_prompt"], "Keep settings when undoing a timeline edit")
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(len(controller.secondary_rows()), 1)
        gui.redo_last_timeline_timing_edit()
        self.assertEqual(len(controller.secondary_rows()), 2)
        self.assertEqual(gui.get_active_segments(), before)

    def test_bilingual_drag_saves_only_the_selected_style_and_keeps_other_ass_track(self):
        from pathlib import Path
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        main_layer = next(t for t in gui.timeline._timeline.tracks if t.name == "TS1").layers[0]
        before_secondary = copy.deepcopy(controller.cfg["styles"]["secondary"])
        main_data = copy.deepcopy(gui.get_active_segments())
        gui.on_timeline_layer_selected(main_layer.id)
        self.assertTrue(gui.video_view.subtitle_item._editable)
        gui.on_subtitle_drag_started()
        self.assertEqual(controller.dragging_role, "primary")
        ass = Path(controller.render_preview(gui.get_active_segments())).read_text(encoding="utf-8-sig")
        self.assertNotIn("Main text", ass)
        self.assertIn("Original text", ass)
        gui.on_subtitle_position_dragged(40, 85)
        self.assertEqual(controller.cfg["styles"]["primary"]["position"]["custom_position_x"], 40)
        self.assertEqual(controller.cfg["styles"]["primary"]["position"]["custom_position_y"], 85)
        self.assertEqual(controller.cfg["styles"]["secondary"], before_secondary)
        before_primary = copy.deepcopy(controller.cfg["styles"]["primary"])
        gui.on_timeline_layer_selected("secondary-0")
        self.assertTrue(gui.video_view.subtitle_item._editable)
        self.assertEqual(gui.video_view.subtitle_item.current_text, "Original text")
        # Drag ownership follows the selected timeline layer, even if the
        # shared style controls are currently displaying the other bank.
        controller.style_role.setCurrentIndex(0)
        gui.on_subtitle_drag_started()
        self.assertEqual(controller.dragging_role, "secondary")
        ass = Path(controller.render_preview(gui.get_active_segments())).read_text(encoding="utf-8-sig")
        self.assertIn("Main text", ass)
        self.assertNotIn("Original text", ass)
        gui.on_subtitle_position_dragged(55, 68)
        self.assertEqual(controller.cfg["styles"]["primary"], before_primary)
        self.assertEqual(controller.cfg["styles"]["secondary"]["position"]["custom_position_y"], 68)
        self.assertEqual(gui.get_active_segments(), main_data)
        self.assertIsNone(controller.dragging_role)
        ass = Path(controller.render_preview(gui.get_active_segments())).read_text(encoding="utf-8-sig")
        self.assertIn("Main text", ass)
        self.assertIn("Original text", ass)
        gui.on_timeline_layer_selected("")
        self.assertFalse(gui.video_view.subtitle_item._editable)

    def test_bilingual_playback_and_locked_layers_disable_drag_without_changing_styles(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        before = copy.deepcopy(controller.cfg["styles"])
        gui.on_timeline_layer_selected("secondary-0")
        gui.on_subtitle_drag_started()
        with patch.object(gui, "_preview_is_playing", return_value=True):
            gui.sync_live_subtitle_preview()
            self.assertFalse(gui.video_view.subtitle_item._editable)
            self.assertIsNone(controller.dragging_role)
        track = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2")
        track.locked = True
        gui.on_timeline_layer_selected("secondary-0")
        self.assertFalse(gui.video_view.subtitle_item._editable)
        self.assertEqual(controller.cfg["styles"], before)

    def test_qt_graphics_overlay_exposes_drag_signals_and_commits_normalized_position(self):
        from PySide6.QtCore import Qt, QPointF
        from PySide6.QtTest import QSignalSpy
        from types import SimpleNamespace
        from ui.widgets.video_view import VideoView
        view = VideoView()
        view.resize(640, 360)
        item = view.subtitle_item
        item.set_editable(True)
        started = QSignalSpy(view.subtitleDragStarted)
        finished = QSignalSpy(view.subtitlePositionChanged)
        def event(point):
            return SimpleNamespace(button=lambda: Qt.LeftButton, scenePos=lambda: point, accept=lambda: None, ignore=lambda: None)
        try:
            item.mousePressEvent(event(QPointF(100, 100)))
            item.mouseMoveEvent(event(QPointF(180, 150)))
            item.mouseReleaseEvent(event(QPointF(180, 150)))
            self.assertEqual(started.count(), 1)
            self.assertEqual(finished.count(), 1)
            self.assertTrue(item.custom_position_enabled)
            self.assertTrue(0 <= item.custom_x_percent <= 100)
            self.assertTrue(0 <= item.custom_y_percent <= 100)
        finally:
            view.deleteLater()

    def test_native_bilingual_drag_reloads_ass_only_at_drag_boundaries(self):
        from types import MethodType
        from unittest.mock import MagicMock
        from ui.main_window import VideoTranslatorGUI
        gui = self.editor()
        gui.video_view.limit_layers_to_video = True
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        from PySide6.QtCore import QSignalBlocker
        blocker = QSignalBlocker(gui.translated_text)
        gui.translated_text.setPlainText(gui.format_to_srt(gui.current_translated_segments))
        del blocker
        gui.performance_mode = False
        gui.media_player = MagicMock()
        gui.media_player.position.return_value = 0
        gui.media_player._sub_track_id = 1
        gui.sync_live_subtitle_preview = MethodType(VideoTranslatorGUI.sync_live_subtitle_preview, gui)
        gui.on_timeline_layer_selected("secondary-0")
        gui.media_player.set_subtitle_file.reset_mock()
        gui.video_view.subtitle_item._drag_active = True
        gui.on_subtitle_drag_started()
        self.assertEqual(gui.media_player.set_subtitle_file.call_count, 1)
        drag_ass = gui.media_player.set_subtitle_file.call_args.args[0]
        from pathlib import Path
        self.assertIn("Main text", Path(drag_ass).read_text(encoding="utf-8-sig"))
        self.assertNotIn("Original text", Path(drag_ass).read_text(encoding="utf-8-sig"))
        from PySide6.QtCore import QPointF, Qt, QEvent
        from PySide6.QtGui import QMouseEvent
        item = gui.video_view.subtitle_item
        item._drag_grab_offset = QPointF()
        move = QMouseEvent(QEvent.MouseMove, QPointF(120, 80), QPointF(120, 80), Qt.NoButton, Qt.LeftButton, Qt.NoModifier)
        with patch.object(gui.video_view, "reposition_subtitle") as reposition:
            item.mouseMoveEvent(move)
            self.assertTrue(item.custom_position_enabled)
            reposition.assert_called_once()
        self.assertEqual(gui.media_player.set_subtitle_file.call_count, 1)
        gui.video_view.subtitle_item._drag_active = False
        gui.on_subtitle_position_dragged(55, 66)
        self.assertEqual(gui.media_player.set_subtitle_file.call_count, 2)
        final_ass = gui.media_player.set_subtitle_file.call_args.args[0]
        self.assertIn("Original text", Path(final_ass).read_text(encoding="utf-8-sig"))
        self.assertIn("Main text", Path(final_ass).read_text(encoding="utf-8-sig"))

    def test_switching_segments_does_not_save_project_or_rebuild_tracks(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        main = next(t for t in gui.timeline._timeline.tracks if t.name == "TS1").layers[0]
        gui.on_timeline_layer_selected("secondary-0")
        with patch.object(gui.project_service, "save_project", wraps=gui.project_service.save_project) as save, \
             patch.object(controller, "sync_tracks", wraps=controller.sync_tracks) as rebuild:
            for _ in range(4):
                gui.on_timeline_layer_selected(main.id)
                gui.on_timeline_layer_selected("secondary-0")
            self.assertEqual(save.call_count, 0)
            self.assertEqual(rebuild.call_count, 0)
        # Real edits must still be committed when leaving that style bank.
        gui.subtitle_font_size_spin.setValue(42)
        with patch.object(gui.project_service, "save_project", wraps=gui.project_service.save_project) as save:
            gui.on_timeline_layer_selected(main.id)
            self.assertGreater(save.call_count, 0)
        self.assertEqual(gui.current_project_state.settings[KEY]["styles"]["secondary"]["size"], 42)

    def test_timeline_cache_isolated_from_edits_and_refreshes_with_timewarps(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        cached = controller.timeline_rows(copy_rows=False)
        self.assertIs(controller.timeline_rows(copy_rows=False), cached)
        editable = controller.timeline_rows()
        editable[0]["text"] = "Not saved"
        self.assertEqual(controller.timeline_rows()[0]["text"], "Original text")
        gui.video_time_warps = [{"type": "freeze", "time": .4, "duration": 1.}]
        self.assertAlmostEqual(controller.timeline_rows()[0]["end"], 1.8)
        gui.video_time_warps[0]["duration"] = 2.
        self.assertAlmostEqual(controller.timeline_rows()[0]["end"], 2.8)
        controller.update_secondary(0, text="Saved")
        self.assertEqual(controller.timeline_rows()[0]["text"], "Saved")
        self.assertEqual(controller.secondary_rows()[0]["text"], "Saved")

    def test_typing_main_text_updates_one_layer_without_full_timeline_refresh(self):
        from PySide6.QtWidgets import QTextEdit
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        main = next(t for t in gui.timeline._timeline.tracks if t.name == "TS1").layers[0]
        secondary = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0]
        editor = QTextEdit()
        editor.setPlainText("Main text edited")
        with patch.object(gui, "apply_segments_to_timeline", wraps=gui.apply_segments_to_timeline) as rebuild, \
             patch.object(gui, "schedule_timeline_visual_refresh") as media_refresh:
            gui.on_segment_translation_edited(0, editor)
            self.assertEqual(rebuild.call_count, 0)
            self.assertEqual(media_refresh.call_count, 0)
        self.assertEqual(main.text, "Main text edited")
        self.assertTrue(main.metadata["_tts_dirty"])
        self.assertEqual(secondary.text, "Original text")
        editor.deleteLater()

    def test_secondary_text_and_prompt_changes_keep_layer_identity_and_layout(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        track = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2")
        layer = track.layers[0]
        with patch.object(gui.timeline, "_redraw", wraps=gui.timeline._redraw) as redraw:
            controller.update_secondary(0, text="Edited")
            controller.cfg["secondary_prompt"] = "Prompt setting only"
            controller.changed()
            self.assertEqual(redraw.call_count, 0)
        self.assertIs(track.layers[0], layer)
        self.assertEqual(layer.text, "Edited")
        controller.move_layer(layer, .2, .9)
        self.assertEqual(track.layers[0].start, .2)

    def test_speaker_timeline_palette_reserves_teal_and_keeps_video_colors(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QColor, QImage, QPainter
        from app.layers.subtitle import SubtitleLayer
        from ui.views.editor.timeline_palette import SECONDARY_SUBTITLE_FILL, speaker_timeline_hue
        gui = self.editor()
        ts2_hue = QColor(SECONDARY_SUBTITLE_FILL).hue()
        for speaker in [f"SPEAKER_{index:02d}" for index in range(1000)] + ["Alice", "Speaker_custom", "SPEAKER_-1"]:
            color = QColor(gui._speaker_timeline_color_hex(speaker))
            difference = abs(color.hue() - ts2_hue)
            self.assertGreaterEqual(min(difference, 360 - difference), 28)
            self.assertFalse(140 <= color.hue() <= 200)
        expected = gui._speaker_timeline_color_hex("SPEAKER_09")
        speaker_timeline_hue.cache_clear()
        self.assertEqual(gui._speaker_timeline_color_hex("SPEAKER_09"), expected)
        self.assertEqual(gui._speaker_color_hex("SPEAKER_09"), QColor.fromHsv(173, 155, 205).name())
        primary = next(t for t in gui.timeline._timeline.tracks if t.name == "TS1").layers[0]
        primary.metadata["_seg_dict"]["speaker"] = "SPEAKER_09"
        secondary = SubtitleLayer(text="Second", start=0., end=1., metadata={"secondary_subtitle": True, "speaker": "SPEAKER_09"})
        for layer, fill in ((primary, expected), (secondary, SECONDARY_SUBTITLE_FILL)):
            image = QImage(240, 60, QImage.Format_ARGB32)
            image.fill(Qt.transparent)
            painter = QPainter(image)
            gui.timeline._draw_standard_layer_bar(painter, layer, 10, 10, 200, 36, 240, False)
            painter.end()
            self.assertEqual(image.pixelColor(30, 14).name(), fill)

    def test_restore_main_timing_uses_completed_run_and_survives_reload_undo_redo(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        gui._pipeline_done()
        gui.current_translated_segments[0]["text"] = "Edited text stays"
        gui.on_timeline_segment_timing_changed(0, .2, .5)
        self.assertTrue(gui.can_restore_subtitle_timing(0))
        secondary = copy.deepcopy(controller.secondary_rows())
        gui.restore_subtitle_timing_btn.click()
        self.assertEqual((gui.current_translated_segments[0]["start"], gui.current_translated_segments[0]["end"]), (0., .8))
        self.assertEqual(gui.current_translated_segments[0]["text"], "Edited text stays")
        self.assertEqual(controller.secondary_rows(), secondary)
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(gui.current_translated_segments[0]["start"], .2)
        gui.redo_last_timeline_timing_edit()
        self.assertEqual(gui.current_translated_segments[0]["start"], 0.)
        state = gui.current_project_state
        gui.load_project_context(state)
        gui.on_timeline_segment_timing_changed(0, .1, .6)
        self.assertTrue(gui.restore_subtitle_timing(0))
        self.assertEqual(gui.current_translated_segments[0]["end"], .8)

    def test_restore_secondary_timing_is_independent_and_respects_locks(self):
        gui = self.editor()
        controller = gui.subtitle_presentation_controller
        controller.mode.setCurrentIndex(2)
        gui.snapshot_subtitle_timing()
        main = copy.deepcopy(gui.current_translated_segments)
        layer = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2").layers[0]
        controller.move_layer(layer, .2, .9)
        controller.select_secondary(0)
        self.assertTrue(controller.inspector.restore_button.isEnabled())
        controller.inspector.restore_button.click()
        self.assertEqual((controller.secondary_rows()[0]["start"], controller.secondary_rows()[0]["end"]), (0., .8))
        self.assertEqual(gui.current_translated_segments, main)
        gui.undo_last_timeline_timing_edit()
        self.assertEqual(controller.secondary_rows()[0]["start"], .2)
        track = next(t for t in gui.timeline._timeline.tracks if t.name == "TS2")
        track.locked = True
        self.assertFalse(controller.restore_timing(0))
        track.locked = False
        with patch.object(gui, "_preview_is_playing", return_value=True):
            self.assertFalse(controller.restore_timing(0))
        self.assertTrue(controller.restore_timing(0))

    def test_track_labels_are_short_without_renaming_model_ids(self):
        from ui.views.editor.track_labels import display_track_name
        from i18n import t
        gui = self.editor()
        names = [track.name for track in gui.timeline._timeline.tracks]
        self.assertEqual(display_track_name("V1 Video"), t("Video"))
        self.assertEqual(display_track_name("A1 Audio"), t("Original audio"))
        self.assertEqual(display_track_name("TS1"), t("Main subtitles"))
        self.assertEqual(display_track_name("TS2"), t("Secondary subtitles"))
        self.assertEqual(display_track_name("A2 Dub"), t("Main subtitles"))
        self.assertEqual(display_track_name("Narration"), "Narration")
        self.assertEqual([track.name for track in gui.timeline._timeline.tracks], names)

    def test_checkpoint_changes_only_after_a_successful_run(self):
        from app.subtitle_timing import restored
        gui = self.editor()
        gui._pipeline_done()
        gui.on_timeline_segment_timing_changed(0, .2, .5)
        gui._pipeline_fail("test interrupted run")
        self.assertEqual(restored(gui.current_translated_segments[0]), (0., .8))
        gui._pipeline_done()
        self.assertEqual(restored(gui.current_translated_segments[0]), (.2, .5))
        gui.on_timeline_segment_timing_changed(0, .1, .6)
        self.assertTrue(gui.restore_subtitle_timing(0))
        self.assertEqual((gui.current_translated_segments[0]["start"], gui.current_translated_segments[0]["end"]), (.2, .5))


if __name__ == "__main__":
    unittest.main()
