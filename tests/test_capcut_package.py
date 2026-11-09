import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from app.capcut.draft_geometry import visual_clip
from app.capcut.draft_package import install_package
from app.capcut.draft_export import DraftExporter, ExportCancelled
from app.capcut.draft_text import text_style
from app.capcut.draft_effects import rectangle_mask


class TextStyleTests(unittest.TestCase):
    def test_size_scales_with_source_size_and_canvas_width(self):
        normal = text_style({"font_size": 48, "font_color": "#2468AC"}, 1080)
        larger = text_style({"font_size": 96}, 1080)
        landscape = text_style({"font_size": 48}, 1920)
        self.assertAlmostEqual(larger["size"], normal["size"] * 2)
        self.assertAlmostEqual(landscape["size"] * 1920, normal["size"] * 1080)
        self.assertEqual(normal["source_size"], 48)
        self.assertEqual(normal["rgb"], [36 / 255, 104 / 255, 172 / 255])

    def test_hex_qcolor_ass_and_invalid_style_inputs(self):
        self.assertEqual(text_style({"font_color": "#3aF"}, 1080)["color"], "#33AAFF")
        qcolor = text_style({"font_color": "#804488CC"}, 1080)
        self.assertEqual(qcolor["color"], "#4488CC")
        self.assertAlmostEqual(qcolor["alpha"], 128 / 255)
        ass = text_style({"font_color": "&H00332211"}, 1080)
        self.assertEqual(ass["color"], "#112233")
        self.assertEqual(ass["alpha"], 1)
        self.assertEqual(text_style({"font_size": float("nan"), "font_color": "invalid"}, 1080)["source_size"], 30)


class GeometryTests(unittest.TestCase):
    def test_region_uses_source_coordinates_under_fit_fill_and_mirror(self):
        region = {"position_x": .6, "position_y": .4, "width": .25, "height": .2}
        for mode in ("fit", "fill"):
            for mirrored in (False, True):
                with self.subTest(mode=mode, mirrored=mirrored):
                    source, canvas = (1920, 1080), (1080, 1920)
                    mask = rectangle_mask(region, source, lambda: "mask", mirrored=mirrored)["config"]
                    clip = visual_clip({"type": "video", "mirror_horizontal": mirrored}, canvas,
                                       {"width": source[0], "height": source[1]},
                                       framing={"mode": mode, "focus": [.7, .3]})
                    fit = min(canvas[0] / source[0], canvas[1] / source[1])
                    shown_w = source[0] * fit * clip["scale"]["x"]
                    shown_h = source[1] * fit * clip["scale"]["y"]
                    x = -mask["centerX"] if mirrored else mask["centerX"]
                    left = canvas[0]/2 + clip["transform"]["x"]*canvas[0]/2 + (x-mask["width"])*shown_w/2
                    top = canvas[1]/2 - clip["transform"]["y"]*canvas[1]/2 - (mask["centerY"]+mask["height"])*shown_h/2
                    image_left = (canvas[0]-shown_w)/2 + clip["transform"]["x"]*canvas[0]/2
                    image_top = (canvas[1]-shown_h)/2 - clip["transform"]["y"]*canvas[1]/2
                    self.assertAlmostEqual(left, image_left + .6 * shown_w)
                    self.assertAlmostEqual(top, image_top + .4 * shown_h)
                    self.assertAlmostEqual(mask["width"] * shown_w, .25 * shown_w)

    def test_region_clamps_like_preview_and_rejects_invalid_numbers(self):
        mask = rectangle_mask({"position_x": .95, "position_y": -.5, "width": .5, "height": .2}, (100, 100), lambda: "m")
        self.assertEqual(mask["config"]["width"], .05)
        self.assertEqual(mask["config"]["centerY"], .8)
        self.assertIsNone(rectangle_mask({"width": 0}, (100, 100), lambda: "m"))
        with self.assertRaises(ValueError):
            rectangle_mask({"position_x": float("nan")}, (100, 100), lambda: "m")

    def test_fill_and_focus_match_source_crop_without_baking(self):
        canvas, media = (1080, 1920), {"width": 1920, "height": 1080}
        layer = {"type": "video"}
        fit = visual_clip(layer, canvas, media)
        fill = visual_clip(layer, canvas, media, framing={"mode": "fill", "focus": (.8, .5)})
        self.assertEqual(fit["scale"], {"x": 1., "y": 1.})
        native_fit = min(1080 / 1920, 1920 / 1080)
        rendered_width = 1920 * native_fit * fill["scale"]["x"]
        rendered_height = 1080 * native_fit * fill["scale"]["y"]
        self.assertAlmostEqual(rendered_height, 1920)
        # Translate the clip into CapCut's half-canvas coordinates. Its left
        # crop must equal 80% of horizontal overflow, as CapCap Fill does.
        left_crop = (rendered_width - 1080) / 2 - fill["transform"]["x"] * 1080 / 2
        self.assertAlmostEqual(left_crop, (rendered_width - 1080) * .8)
        mirrored = visual_clip({"type": "video", "mirror_horizontal": True}, canvas, media,
                               framing={"mode": "fill", "focus": (.8, .5)})
        self.assertAlmostEqual(mirrored["transform"]["x"], -fill["transform"]["x"])

    def test_overlay_rect_rotation_opacity_and_video_offset(self):
        canvas, media = (1080, 1920), {"width": 300, "height": 200}
        layer = {"type": "image", "opacity": .4,
                 "transform": {"x": 60, "y": 25, "scale_x": 1.5, "scale_y": .5, "rotation": 25}}
        clip = visual_clip(layer, canvas, media)
        self.assertAlmostEqual(clip["transform"]["x"], .5)
        self.assertAlmostEqual(clip["transform"]["y"], .4)
        fit = min(1080 / 300, 1920 / 200)
        self.assertAlmostEqual(300 * fit * clip["scale"]["x"], 1080 * .3)
        self.assertAlmostEqual(200 * fit * clip["scale"]["y"], 1920 * .1)
        self.assertEqual(clip["alpha"], .4)
        self.assertEqual(clip["rotation"], 25)
        video = visual_clip({"type": "video", "transform": {"x": 108, "y": 192}}, canvas, media)
        self.assertAlmostEqual(video["transform"]["x"], .2)
        self.assertAlmostEqual(video["transform"]["y"], -.2)


class PortablePackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "old_machine" / "package"
        (self.source / "media").mkdir(parents=True)
        (self.source / "media/video.mp4").write_bytes(b"test packaged video")
        (self.source / "media/voice.wav").write_bytes(b"test packaged voice")
        self.draft = {"id": "A123", "duration": 3000000, "tracks": [{"id": "track", "type": "audio", "segments": [
            {"material_id": "audio", "target_timerange": {"start": 2000000, "duration": 1000000}, "speed": 1.}]}],
            "materials": {"videos": [{"id": "video", "path": str(self.source / "media/video.mp4")}],
                          "audios": [{"id": "audio", "path": str(self.source / "media/voice.wav")}]}}
        for filename, content in (
            ("draft_content.json", self.draft),
            ("Timelines/A123/draft_content.json", self.draft),
            ("Timelines/project.json", {"main_timeline_id": "A123", "timelines": [{"id": "A123"}]}),
            ("capcap_export_report.json", {"warnings": ["Default text style"]}),
            ("capcap_package.json", {"format": "capcap-capcut-package", "version": 1,
                                     "media": [{"material_id": "video", "relative_path": "media/video.mp4"},
                                               {"material_id": "audio", "relative_path": "media/voice.wav"}]}),
        ):
            self.write(filename, content)

    def write(self, name, content):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content), encoding="utf-8")

    def test_moved_package_relinks_every_mirror_and_generates_independent_ids(self):
        old = self.source
        moved = self.root / "new_machine" / "moved"
        moved.parent.mkdir()
        self.source.rename(moved)
        self.source = moved
        original = (moved / "draft_content.json").read_bytes()
        destination = self.root / "capcut_drafts" / "new_project"
        result = install_package(moved, destination)
        self.assertEqual(result["media"], 2)
        draft = json.loads((destination / "draft_content.json").read_text())
        self.assertNotEqual(draft["id"], "A123")
        self.assertEqual(draft["tracks"], self.draft["tracks"])
        project = json.loads((destination / "Timelines/project.json").read_text())
        self.assertEqual(project["main_timeline_id"], draft["id"])
        for file in ("draft_content.json.bak", f"Timelines/{draft['id']}/template.tmp"):
            self.assertEqual(json.loads((destination / file).read_text()), draft)
        for group in ("videos", "audios"):
            for material in draft["materials"][group]:
                self.assertTrue(Path(material["path"]).is_file())
                self.assertTrue(Path(material["path"]).is_relative_to(destination))
                self.assertNotIn(str(old).replace("\\", "/"), material["path"])
        self.assertEqual((moved / "draft_content.json").read_bytes(), original)
        # The installed folder is also portable, so a second relocation works.
        second = self.root / "another_machine" / "second"
        install_package(destination, second)
        self.assertTrue((second / "media/video.mp4").is_file())

    def test_missing_media_traversal_and_external_references_are_rejected(self):
        manifest = json.loads((self.source / "capcap_package.json").read_text())
        for relative in ("../outside.mp4", "C:/outside.mp4", "/outside.mp4", "media/missing.mp4"):
            with self.subTest(relative=relative):
                changed = copy.deepcopy(manifest)
                changed["media"][0]["relative_path"] = relative
                self.write("capcap_package.json", changed)
                destination = self.root / "invalid"
                with self.assertRaises(ValueError):
                    install_package(self.source, destination)
                self.assertFalse(destination.exists())

    def test_cancel_and_existing_destination_preserve_source(self):
        destination = self.root / "cancelled"
        with self.assertRaises(ExportCancelled):
            install_package(self.source, destination, cancelled=lambda: True)
        self.assertFalse(destination.exists())
        self.assertFalse(list(self.root.glob(".capcap-import-*")))
        with self.assertRaises(ValueError):
            install_package(self.source, self.source)

    def test_native_blur_survives_import_without_old_machine_effect_paths(self):
        draft = copy.deepcopy(self.draft)
        draft["materials"]["video_effects"] = [{"id": "blur", "resource_id": "6739752823140913675",
                                                "effect_id": "15206412", "path": "C:/old-machine/CapCut/Cache/blur"}]
        draft["materials"]["common_mask"] = [{"id": "mask", "resource_id": "7374021450748924432",
                                               "path": "C:/old-machine/CapCut/Cache/mask", "config": {"width": .3}}]
        self.write("Timelines/A123/draft_content.json", draft)
        destination = self.root / "native_blur_import"
        result = install_package(self.source, destination)
        restored = json.loads((destination / "draft_content.json").read_text())
        blur = restored["materials"]["video_effects"][0]
        self.assertEqual(blur["resource_id"], "6739752823140913675")
        self.assertNotIn("path", blur)
        self.assertEqual(result["native_blur_count"], 1)
        mask = restored["materials"]["common_mask"][0]
        self.assertNotIn("path", mask)
        self.assertEqual(mask["config"], {"width": .3})
        self.assertEqual(result["native_mask_count"], 1)

    def test_overlay_clips_resolve_source_offsets_without_mutating_layers(self):
        layer = {"id": "video", "type": "video", "start": 0., "end": 8., "source_start": 0.}
        original = copy.deepcopy(layer)
        track = {"layers": [layer], "clips": [
            {"layer_id": "video", "track_start": 1., "source_start": 2., "duration": 3., "speed": .5},
            {"layer_id": "video", "track_start": 7., "source_start": 5., "duration": 1., "speed": 1.},
        ]}
        layers = DraftExporter._layers(track, {"video": layer})
        self.assertEqual([(x["start"], x["end"], x["source_start"], x["speed"]) for x in layers],
                         [(1., 4., 2., .5), (7., 8., 5., 1.)])
        self.assertEqual(layer, original)
