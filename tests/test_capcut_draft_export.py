import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))

from app.capcut.draft_export import DraftExporter, ExportCancelled, video_parts, register_local_draft


FFMPEG = ROOT / "bin/ffmpeg/ffmpeg.exe"
FFPROBE = ROOT / "bin/ffmpeg/ffprobe.exe"


class TimeMappingTests(unittest.TestCase):
    def test_overlapping_asr_cues_preserve_fit_duration_without_replaying_frames(self):
        warps = [
            {"type": "slow", "media_start": 3., "media_end": 5.028, "duration": 1.24},
            {"type": "slow", "media_start": 4.968, "media_end": 5.568, "duration": .49},
            {"type": "slow", "media_start": 5.508, "media_end": 6.408, "duration": 1.65},
        ]
        before = copy.deepcopy(warps)
        warnings = []
        parts = video_parts(10, warps, warnings.append)
        self.assertEqual(warps, before)
        self.assertAlmostEqual(sum(p[2] for p in parts), 10 + 1.24 + .49 + 1.65)
        self.assertAlmostEqual(sum(p[2] * p[3] for p in parts), 10)
        for a, b in zip(parts, parts[1:]):
            self.assertAlmostEqual(a[0] + a[2] * a[3], b[0])
            self.assertAlmostEqual(a[1] + a[2], b[1])
        self.assertEqual(len(warnings), 2)

    def test_fit_voice_preserves_tail_and_exact_slow_duration(self):
        parts = video_parts(10, [
            {"type": "freeze", "time": 2, "duration": .6},
            {"type": "slow", "media_start": 4, "media_end": 6, "duration": 1.4, "speed": .588},
        ])
        self.assertAlmostEqual(sum(p[2] for p in parts), 12)
        self.assertEqual(parts[-1], (6., 8., 4., 1., False))
        for a, b in zip(parts, parts[1:]):
            self.assertAlmostEqual(a[1] + a[2], b[1])
        self.assertAlmostEqual(parts[3][2] * parts[3][3], 2)

    def test_invalid_out_of_range_warps_fail_instead_of_desynchronizing(self):
        with self.assertRaises(ValueError):
            video_parts(10, [{"type": "slow", "media_start": 1, "media_end": 15, "duration": 2}])


@unittest.skipUnless(FFMPEG.is_file() and FFPROBE.is_file(), "Bundled FFmpeg required")
class DraftExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.video, cls.voice = cls.root / "source.mp4", cls.root / "voice.wav"
        def run(*args):
            subprocess.run([str(FFMPEG), "-v", "error", "-y", *args], check=True,
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        run("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=10:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6", "-c:v", "libx264", "-c:a", "aac", str(cls.video))
        run("-f", "lavfi", "-i", "sine=frequency=880:duration=8", str(cls.voice))

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.exporter = DraftExporter(ffmpeg=FFMPEG, ffprobe=FFPROBE)
        self.snapshot = {
            "source_video": str(self.video), "original_audio": str(self.video), "original_volume": .5,
            "voice_audio": str(self.voice), "voice_volume": 1., "mirror_horizontal": True,
            "warps": [{"type": "freeze", "time": 1, "duration": .6},
                      {"type": "slow", "media_start": 2, "media_end": 4, "duration": 1.4, "speed": .588}],
            "timeline": {"duration": 8, "composition": {"width": 1080, "height": 1920, "fps": 30}, "tracks": [
                {"name": "V1 Video", "type": "video", "layers": []},
                {"name": "A1 Audio", "type": "audio", "layers": []},
                {"name": "TS1", "type": "dub_subtitle", "layers": [
                    {"type": "dub_subtitle", "start": 2., "end": 3., "text": "Xin chào 👋", "voice_speed": 1.5,
                     "metadata": {"_audio_end": 3.2, "api_key": "DO_NOT_EXPORT"}},
                    {"type": "dub_subtitle", "start": 7., "end": 8., "text": "Cuối video", "metadata": {"_audio_end": 8.}},
                ]},
                {"name": "B1", "type": "blur", "layers": [{"type": "blur", "start": 0, "end": 8}]},
            ]},
            "api_key": "DO_NOT_EXPORT", "settings": {"gemini_api_key": "DO_NOT_EXPORT"},
        }

    def export(self):
        destination = self.root / next(tempfile._get_candidate_names())
        result = self.exporter.export(self.snapshot, destination)
        self.addCleanup(lambda: __import__('shutil').rmtree(destination, ignore_errors=True))
        draft = json.loads((destination / "draft_content.json").read_text(encoding="utf-8"))
        return destination, draft, result

    def test_real_media_package_native_text_split_voice_and_all_draft_mirrors(self):
        before = copy.deepcopy(self.snapshot)
        destination, draft, result = self.export()
        self.assertEqual(before, self.snapshot)
        self.assertEqual(draft["duration"], 8_000_000)
        self.assertEqual(draft["canvas_config"]["height"], 1920)
        timeline_id = draft["id"]
        for filename in ("template-2.tmp", f"Timelines/{timeline_id}/draft_content.json", f"Timelines/{timeline_id}/template.tmp"):
            self.assertEqual(draft, json.loads((destination / filename).read_text(encoding="utf-8")))
        voice = next(t for t in draft["tracks"] if t["name"] == "TS1 Audio")["segments"]
        self.assertEqual(voice[0]["source_timerange"], {"start": 2_000_000, "duration": 1_200_000})
        self.assertEqual(voice[0]["speed"], 1.)  # processed voice must not be sped up again
        self.assertEqual(voice[-1]["target_timerange"], {"start": 7_000_000, "duration": 1_000_000})
        self.assertEqual(json.loads(draft["materials"]["texts"][0]["content"])["text"], "Xin chào 👋")
        for key in ("videos", "audios"):
            for material in draft["materials"][key]:
                self.assertTrue(Path(material["path"]).is_file())
                self.assertTrue(Path(material["path"]).is_relative_to(destination / "media"))
                self.assertNotIn(".capcap-draft-", material["path"])
        # The video and its extracted audio are distinct usable files.
        self.assertTrue(all(Path(m["path"]).suffix == ".wav" for m in draft["materials"]["audios"]))
        for file in destination.rglob("*.json"):
            self.assertNotIn("DO_NOT_EXPORT", file.read_text(encoding="utf-8"))
        self.assertEqual(result["native_blur_count"], len(draft["tracks"][0]["segments"]))
        self.assertTrue(any("rectangles are transferred" in w for w in result["warnings"]))
        effect = draft["materials"]["video_effects"][0]
        self.assertEqual(effect["resource_id"], "6739752823140913675")
        self.assertIn("00:00:07,000 --> 00:00:08,000", (destination / "subtitles.srt").read_text(encoding="utf-8-sig"))
        video = draft["tracks"][0]["segments"]
        self.assertTrue(all(s["volume"] == 0 and s["clip"]["flip"]["horizontal"] for s in video))

    def test_music_loop_and_mute_and_overlapping_captions(self):
        self.snapshot["music"] = [{"id": "music1", "path": str(self.voice), "start": 0, "end": 18,
                                    "volume": 30, "loop": True, "track_name": "Music"}]
        self.snapshot["timeline"]["tracks"][2]["layers"].append({"type": "dub_subtitle", "start": 2.5, "end": 3.5, "text": "Overlap", "muted": True})
        _, draft, _ = self.export()
        music = next(t for t in draft["tracks"] if t["name"] == "Music")["segments"]
        self.assertEqual(len(music), 3)
        self.assertEqual(music[-1]["source_timerange"]["duration"], 2_000_000)
        self.assertTrue(all(s["volume"] == .3 for s in music))
        self.assertEqual(len([t for t in draft["tracks"] if t["type"] == "text"]), 2)
        self.assertEqual(len(draft["materials"]["audios"]), 2)  # one voice material reused by music

    def test_cancel_and_missing_media_leave_no_partial_directory(self):
        destination = self.root / "cancelled"
        before = set(self.root.iterdir())
        self.exporter.cancelled = lambda: True
        with self.assertRaises(ExportCancelled):
            self.exporter.export(self.snapshot, destination)
        self.assertEqual(set(self.root.iterdir()), before)
        self.exporter.cancelled = lambda: False
        self.snapshot["voice_audio"] = str(self.root / "missing.wav")
        with self.assertRaises(FileNotFoundError):
            self.exporter.export(self.snapshot, destination)
        self.assertEqual(set(self.root.iterdir()), before)

    def test_existing_draft_never_overwritten(self):
        with self.assertRaises(FileExistsError):
            self.exporter.export(self.snapshot, self.root)

    def test_native_blur_timing_visibility_and_mosaic(self):
        blur_track = self.snapshot["timeline"]["tracks"][3]
        blur_track["layers"] = [
            {"type": "blur", "start": 2.5, "end": 4.25, "blur_strength": 45, "blur_opacity": .8,
             "position_x": .125, "position_y": .5, "width": .25, "height": .25},
            {"type": "blur", "start": 3, "end": 5, "pixelate": True, "pixelate_size": 20},
            {"type": "blur", "start": 0, "end": 8, "visible": False},
            {"type": "blur", "start": 0, "end": 8, "opacity": 0},
        ]
        destination, draft, result = self.export()
        self.assertEqual(result["native_blur_count"], 3)
        effects = draft["materials"]["video_effects"]
        self.assertEqual([m["name"] for m in effects], ["Blur", "Blur", "Mosaic"])
        self.assertEqual(effects[0]["adjust_params"][0]["value"], .45)
        self.assertEqual(effects[0]["value"], 1.)  # opacity belongs to overlay, applied once
        self.assertTrue(all(m["apply_target_type"] == 0 for m in effects))
        self.assertNotIn("path", effects[0])
        self.assertFalse(any(t["type"] == "effect" for t in draft["tracks"]))
        effect_tracks = [t for t in draft["tracks"] if t["name"].startswith("B1 Regions")]
        self.assertEqual(len(effect_tracks), 2)  # overlapping effects get separate editable lanes
        segment = effect_tracks[0]["segments"][0]
        self.assertEqual(segment["target_timerange"], {"start": 2500000, "duration": 100000})
        self.assertEqual(segment["source_timerange"], {"start": 1900000, "duration": 100000})
        self.assertEqual(segment["clip"]["alpha"], .8)
        self.assertEqual(segment["volume"], 0.)
        self.assertIn(effects[0]["id"], segment["extra_material_refs"])
        mask = draft["materials"]["common_mask"][0]
        self.assertIn(mask["id"], segment["extra_material_refs"])
        self.assertEqual(mask["config"]["centerX"], .5)  # material-space mirror compensation
        self.assertEqual(mask["config"]["centerY"], -.25)
        self.assertEqual(mask["config"]["width"], .25)
        self.assertEqual(mask["config"]["height"], .25)
        slow = effect_tracks[0]["segments"][1]
        self.assertEqual(slow["target_timerange"], {"start": 2600000, "duration": 1650000})
        self.assertAlmostEqual(slow["speed"], 2 / 3.4)
        # Blur is below text; it cannot replace subtitles with a source copy.
        text_index = next(i for i,t in enumerate(draft["tracks"]) if t["type"] == "text")
        self.assertLess(draft["tracks"].index(effect_tracks[-1]), text_index)
        report = json.loads((destination / "capcap_layers.json").read_text())
        self.assertTrue(any(x["status"] == "native_region" and x["region_transferred"] for x in report["layers"]))
        blur_track["visible"] = False
        _, hidden, _ = self.export()
        self.assertFalse(hidden["materials"]["video_effects"])

    def test_blur_inside_freeze_reuses_photo_and_solid_mask_is_packaged(self):
        from PIL import Image
        self.snapshot["framing"] = {"mode": "fill", "focus": [.7, .3]}
        self.snapshot["timeline"]["tracks"][3]["layers"] = [
            {"type": "blur", "start": 1.2, "end": 1.4, "position_x": .2, "position_y": .4, "width": .3, "height": .2}]
        self.snapshot["timeline"]["tracks"].append({"name": "M1", "type": "mask", "layers": [
            {"type": "mask", "mode": "solid", "color": "#2468AC", "opacity": .6,
             "start": 2.9, "end": 4.1, "position_x": .1, "position_y": .2, "width": .3, "height": .25}]})
        destination, draft, _ = self.export()
        main = draft["tracks"][0]["segments"]
        freeze = next(t for t in draft["tracks"] if t["name"] == "B1 Regions")["segments"][0]
        self.assertEqual(freeze["material_id"], main[1]["material_id"])
        self.assertEqual(freeze["clip"], main[1]["clip"])
        self.assertEqual(freeze["target_timerange"], {"start": 1200000, "duration": 200000})
        self.assertEqual(freeze["source_timerange"], {"start": 0, "duration": 200000})
        self.assertEqual(freeze["speed"], 1)
        solid = next(t for t in draft["tracks"] if t["name"] == "M1 Regions")["segments"][0]
        material = next(m for m in draft["materials"]["videos"] if m["id"] == solid["material_id"])
        with Image.open(material["path"]) as image:
            self.assertEqual(image.size, (64, 48))
            self.assertEqual(image.getpixel((0, 0)), (36, 104, 172, 255))
        self.assertEqual(solid["clip"]["alpha"], .6)
        self.assertEqual(solid["speed"], 1)
        self.assertEqual(solid["volume"], 0)
        from app.capcut.draft_package import install_package
        imported = destination.parent / (destination.name + "_imported")
        self.addCleanup(lambda: __import__('shutil').rmtree(imported, ignore_errors=True))
        install_package(destination, imported)
        restored = json.loads((imported / "draft_content.json").read_text())
        self.assertEqual(restored["materials"]["common_mask"], draft["materials"]["common_mask"])
        self.assertEqual(restored["materials"]["video_effects"], draft["materials"]["video_effects"])
        self.assertEqual(restored["tracks"], draft["tracks"])
        self.assertTrue(all(Path(m["path"]).is_file() for m in restored["materials"]["videos"]))

    def test_mask_blur_pixelate_hidden_and_outside_video(self):
        self.snapshot["timeline"]["tracks"][3]["visible"] = False
        self.snapshot["timeline"]["tracks"].append({"name": "M1", "type": "mask", "layers": [
            {"type": "mask", "mode": "blur", "start": 3, "end": 4},
            {"type": "mask", "mode": "pixelate", "start": 4, "end": 5},
            {"type": "mask", "mode": "solid", "start": 1, "end": 2, "visible": False},
            {"type": "mask", "mode": "solid", "start": 9, "end": 10},
        ]})
        _, draft, _ = self.export()
        self.assertEqual([m["name"] for m in draft["materials"]["video_effects"]], ["Blur", "Mosaic"])
        regions = next(t for t in draft["tracks"] if t["name"] == "M1 Regions")["segments"]
        self.assertEqual(len(regions), 2)
        self.assertEqual(draft["duration"], 8_000_000)

    def test_fill_freezes_custom_subtitle_position_and_overlay_clips_are_editable(self):
        self.snapshot["framing"] = {"mode": "fill", "focus": [.8, .5]}
        self.snapshot["subtitle_position"] = {"custom_position_enabled": True, "custom_position_x": 35, "custom_position_y": 75}
        self.snapshot["timeline"]["tracks"].append({"name": "Overlay", "type": "video", "layers": [
            {"id": "overlay", "type": "video", "source": str(self.video), "start": 0, "end": 6,
             "volume": .35, "opacity": .6, "transform": {"x": 108, "y": 192, "rotation": 15}},
        ], "clips": [{"layer_id": "overlay", "track_start": 1, "source_start": 2, "duration": 2, "speed": .5}]})
        destination, draft, _ = self.export()
        main = draft["tracks"][0]["segments"]
        self.assertGreater(main[0]["clip"]["scale"]["x"], 1)
        # Freeze image and moving video retain identical framing.
        self.assertTrue(all(s["clip"] == main[0]["clip"] for s in main))
        overlay = next(t for t in draft["tracks"] if t["name"] == "Overlay")["segments"][0]
        self.assertEqual(overlay["target_timerange"], {"start": 1000000, "duration": 2000000})
        self.assertEqual(overlay["source_timerange"], {"start": 2000000, "duration": 1000000})
        self.assertEqual(overlay["clip"]["transform"], {"x": .2, "y": -.2})
        self.assertEqual(overlay["volume"], .35)
        subtitle = next(t for t in draft["tracks"] if t["name"] == "TS1")["segments"][0]
        self.assertAlmostEqual(subtitle["clip"]["transform"]["x"], -.3)
        self.assertAlmostEqual(subtitle["clip"]["transform"]["y"], -.5)
        self.assertTrue((destination / "capcap_package.json").is_file())
        report = json.loads((destination / "capcap_layers.json").read_text())
        self.assertTrue(any(x["name"] == "Overlay" and x["status"] == "converted" for x in report["layers"]))

    def test_individual_processed_voice_files_are_not_read_at_timeline_offsets(self):
        # A per-cue WAV already contains its applied voice speed. The native
        # CapCut clip must start at source zero even when the cue starts at 7s.
        layers = self.snapshot["timeline"]["tracks"][2]["layers"]
        for layer in layers:
            layer["_voice_file"] = str(self.voice)
        _, draft, _ = self.export()
        clips = next(t for t in draft["tracks"] if t["name"] == "TS1 Audio")["segments"]
        self.assertTrue(all(x["source_timerange"]["start"] == 0 and x["speed"] == 1 for x in clips))
        self.assertEqual(clips[-1]["target_timerange"]["start"], 7000000)

    def test_file_sticker_is_a_separate_editable_image_layer(self):
        image = self.root / "sticker.png"
        subprocess.run([str(FFMPEG), "-v", "error", "-y", "-i", str(self.video), "-frames:v", "1", str(image)],
                       capture_output=True, check=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.snapshot["timeline"]["tracks"].append({"name": "Sticker", "type": "sticker", "layers": [
            {"type": "sticker", "source": str(image), "start": 1, "end": 4,
             "opacity": .5, "transform": {"x": 60, "y": 20, "scale_x": 1, "scale_y": 1}},
        ]})
        _, draft, _ = self.export()
        segment = next(t for t in draft["tracks"] if t["name"] == "Sticker")["segments"][0]
        material = next(m for m in draft["materials"]["videos"] if m["id"] == segment["material_id"])
        self.assertEqual(material["type"], "photo")
        self.assertEqual(segment["clip"]["alpha"], .5)
        self.assertAlmostEqual(segment["clip"]["transform"]["x"], .4)

    def test_subtitles_and_text_keep_sizes_colors_and_portable_style_fields(self):
        cues = self.snapshot["timeline"]["tracks"][2]["layers"]
        cues[0].update(font_size=60, font_color="#12AB34")
        cues[1].update(font_size=30, font_color="#CC5522")
        self.snapshot["timeline"]["tracks"].append({"name": "Title", "type": "text", "layers": [
            {"type": "text", "start": 0, "end": 3, "text": "Title", "font_size": 72, "font_color": "#0088FF"},
        ]})
        destination, draft, _ = self.export()
        materials = draft["materials"]["texts"]
        sizes = []
        for material, expected in zip(materials, ("#12AB34", "#CC5522", "#0088FF")):
            style = json.loads(material["content"])["styles"][0]
            self.assertEqual(material["text_color"], expected)
            self.assertEqual(style["fill"]["content"]["solid"]["color"], [int(expected[i:i+2], 16) / 255 for i in (1, 3, 5)])
            self.assertEqual(material["font_size"], style["size"])
            self.assertEqual(material["text_size"], 30)  # native compatibility marker, not source font size
            sizes.append(style["size"])
        self.assertAlmostEqual(sizes[0], sizes[1] * 2)
        self.assertGreater(sizes[2], sizes[0])
        from app.capcut.draft_package import install_package
        imported = destination.parent / (destination.name + "_imported")
        self.addCleanup(lambda: __import__('shutil').rmtree(imported, ignore_errors=True))
        install_package(destination, imported)
        restored = json.loads((imported / "draft_content.json").read_text(encoding="utf-8"))
        self.assertEqual(restored["materials"]["texts"], materials)

    def test_silent_source_and_image_and_text_layers(self):
        silent = self.root / "silent.mp4"
        image = self.root / "logo.png"
        for args in (["-i", str(self.video), "-an", "-c:v", "copy", str(silent)],
                     ["-i", str(self.video), "-frames:v", "1", str(image)]):
            subprocess.run([str(FFMPEG), "-v", "error", "-y", *args], check=True, capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.snapshot.update(source_video=str(silent), original_audio=str(silent))
        self.snapshot["timeline"]["tracks"].extend([
            {"name": "Logo", "type": "image", "layers": [{"type": "image", "source": str(image),
                "start": .5, "end": 5, "transform": {"x": 10, "y": 20, "scale_x": 1, "scale_y": 1}}]},
            {"name": "Text", "type": "text", "layers": [{"type": "text", "text": "Title", "start": 0, "end": 3,
                "transform": {"x": .25, "y": .3}}]},
        ])
        _, draft, result = self.export()
        self.assertFalse(any(t["name"] == "A1 Original" for t in draft["tracks"]))
        logo = next(t for t in draft["tracks"] if t["name"] == "Logo")["segments"][0]
        self.assertAlmostEqual(logo["clip"]["transform"]["x"], -.6)
        title = next(t for t in draft["tracks"] if t["name"] == "Text")["segments"][0]
        self.assertEqual(title["clip"]["transform"], {"x": -.5, "y": .4})
        self.assertTrue(any("no original audio" in w for w in result["warnings"]))

    def test_registration_preserves_index_and_backup_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            original = b'{"all_draft_store":[{"draft_id":"existing","keep":"unchanged"}],"draft_ids":1,"unknown":42}'
            (root / "root_meta_info.json").write_bytes(original)
            destination = root / "new_draft"
            self.exporter.export(self.snapshot, destination)
            register_local_draft(destination)
            register_local_draft(destination)
            index = json.loads((root / "root_meta_info.json").read_text(encoding="utf-8"))
            self.assertEqual(index["unknown"], 42)
            self.assertEqual(index["all_draft_store"][0], {"draft_id": "existing", "keep": "unchanged"})
            self.assertEqual(index["draft_ids"], 2)
            self.assertEqual(len(index["all_draft_store"]), 2)
            backups = list(root.glob("capcap_index_before_*.bak"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), original)
            self.assertFalse(list(destination.glob("capcap_index_before_*.bak")))

    def test_hidden_subtitles_keep_voice_and_full_import_is_not_duplicated(self):
        self.snapshot["timeline"]["tracks"][2]["visible"] = False
        _, draft, _ = self.export()
        self.assertFalse(any(t["type"] == "text" for t in draft["tracks"]))
        self.assertTrue(any(t["name"] == "TS1 Audio" for t in draft["tracks"]))
        self.snapshot["voice_full_track"] = True
        _, draft, _ = self.export()
        self.assertTrue(any(t["name"] == "Imported Voice" for t in draft["tracks"]))
        self.assertFalse(any(t["name"] == "TS1 Audio" for t in draft["tracks"]))

    def test_background_dialog_and_live_snapshot(self):
        import os
        from types import SimpleNamespace
        from unittest.mock import patch, MagicMock
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        sys.path[:0] = [str(ROOT / "ui"), str(ROOT / "app"), str(ROOT)]
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QSettings
        from ui.main_window import VideoTranslatorGUI
        from ui.controllers.capcut_export_controller import capture_snapshot, _ExportDialog
        from app.layers.timeline import Timeline, Track, Clip
        from app.layers.dub_subtitle import DubSubtitleLayer
        from app.layers.blur import BlurLayer
        from app.layers.mask import MaskLayer
        from app.layers.audio import AudioLayer
        from app.layers.base import LayerType
        from services.project_service import ProjectService
        app = QApplication.instance() or QApplication([])
        app._capcap_test_mode = True
        with tempfile.TemporaryDirectory() as folder, \
                patch("ui.main_window.QTimer.singleShot"), \
                patch.object(VideoTranslatorGUI, "_preload_active_voice_if_needed"), \
                patch("requests.sessions.Session.request", side_effect=AssertionError("HTTP disabled")):
            QSettings.setDefaultFormat(QSettings.IniFormat)
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, folder)
            gui = VideoTranslatorGUI()
            try:
                self.assertTrue(hasattr(gui, "export_capcut_action"))
                self.assertFalse(hasattr(gui, "import_capcut_action"))
                self.assertEqual(len(gui.resource_export_menu.actions()), 2)
                self.assertFalse(hasattr(gui, "resource_import_menu"))
                from i18n import t
                self.assertEqual(gui.resource_export_menu.title(), t("Export Resources"))
                self.assertTrue(hasattr(gui, "export_others_action"))
                self.assertFalse(hasattr(gui, "import_capcap_action"))
                gui.project_service = ProjectService(folder)
                gui.current_project_state = gui.project_service.ensure_project(str(self.video))
                layer = DubSubtitleLayer(start=1, end=2, text="Live edited subtitle", metadata={"_audio_end": 2})
                gui.timeline._timeline = Timeline(duration=6, tracks=[Track(name="TS1", type=LayerType.DUB_SUBTITLE, layers=[layer])])
                gui._resolve_preview_original_video_path = lambda: str(self.video)
                gui._resolve_preview_original_audio_path = lambda: str(self.video)
                gui._resolve_preview_voice_only_audio_path = lambda: str(self.voice)
                gui.using_existing_audio_source = lambda: False
                gui._music_audio_tracks = lambda: []
                gui.get_output_fps_key = lambda: "source"
                gui.subtitle_font_size_spin.blockSignals(True)
                gui.subtitle_font_size_spin.setValue(72)
                gui.subtitle_font_size_spin.blockSignals(False)
                gui.subtitle_color_hex = "#2478AB"
                gui._uses_speaker_subtitle_colors = lambda: False
                gui.preview_controller._resolve_output_canvas_dimensions = lambda _: (None, None)
                snapshot = capture_snapshot(gui)
                captured = snapshot["timeline"]["tracks"][0]["layers"][0]
                self.assertEqual(captured["font_size"], 72)
                self.assertEqual(captured["font_color"].upper(), "#2478AB")
                layer.metadata["_seg_dict"] = {"speaker": "SPEAKER_00"}
                gui._uses_speaker_subtitle_colors = lambda: True
                gui._speaker_color_hex = lambda _: "#FF8833"
                self.assertEqual(capture_snapshot(gui)["timeline"]["tracks"][0]["layers"][0]["font_color"].upper(), "#FF8833")
                layer.text = "A later edit must not alter the export snapshot"
                self.assertEqual(snapshot["timeline"]["tracks"][0]["layers"][0]["text"], "Live edited subtitle")
                gui.timeline._timeline.tracks.extend([
                    Track(name="B1", type=LayerType.BLUR, layers=[BlurLayer(start=0, end=6)]),
                    Track(name="M1", type=LayerType.MASK, layers=[MaskLayer(start=0, end=6)])])
                gui._blur_effect_enabled = lambda: False
                gui._mask_track_preview_visible = False
                hidden = capture_snapshot(gui)
                self.assertFalse(hidden["timeline"]["tracks"][1]["visible"])
                self.assertFalse(hidden["timeline"]["tracks"][2]["visible"])
                gui._blur_effect_enabled = lambda: True
                gui._mask_track_preview_visible = True
                shown = capture_snapshot(gui)
                self.assertTrue(shown["timeline"]["tracks"][1]["visible"])
                self.assertTrue(shown["timeline"]["tracks"][2]["visible"])
                music_layer = AudioLayer(source=str(self.voice), start=2, end=6, fade_in=1., pan=.4)
                music_track = Track(name="A2 Music", type=LayerType.AUDIO, layers=[music_layer],
                                    metadata={"_gain_db": 6., "_fade_in": 2.},
                                    clips=[Clip(layer_id=music_layer.id, track_start=3, source_start=1, duration=2, speed=.5)])
                gui.timeline._timeline.tracks.append(music_track)
                gui._music_audio_tracks = lambda: [{"id": music_layer.id, "path": str(self.voice), "start": 2., "end": 6., "volume": 40., "loop": True}]
                music_snapshot = capture_snapshot(gui)["music"][0]
                self.assertEqual((music_snapshot["start"], music_snapshot["end"], music_snapshot["source_start"]), (3., 5., 1.))
                self.assertEqual(music_snapshot["layer"]["pan"], .4)
                self.assertEqual(music_snapshot["layer"]["fade_in"], 2.)
                self.assertEqual(music_snapshot["track_metadata"]["_fade_in"], 2.)
                self.assertAlmostEqual(music_snapshot["volume"], 40*10**.3)
                style_snapshot = capture_snapshot(gui)["timeline"]["tracks"][0]["layers"][0]
                self.assertEqual(style_snapshot["font_name"], gui.get_subtitle_export_style(segments=[])["font_name"])
                self.assertEqual(style_snapshot["font_bold"], gui.subtitle_bold_cb.isChecked())
                from ui.controllers.resource_export_controller import capture_resources
                from app.services.resource_package import build_catalog
                gui.current_segments = [{"start": 0, "end": 1, "text": "Source text"}]
                gui.current_translated_segments = [{"start": 1, "end": 2, "text": "Translated text", "_wav_path": str(self.voice)}]
                resources = capture_resources(gui)
                catalog = build_catalog(resources)
                ids = {i["id"] for i in catalog}
                self.assertTrue({"source_srt", "translated_srt", "tts_1", "timeline", "source_video", "editor"}.issubset(ids))
                destination = Path(folder) / "native_dialog_export"
                dialog = _ExportDialog(snapshot, destination, gui)
                dialog.worker.start()
                dialog.exec()
                self.assertFalse(dialog.worker.isRunning())
                self.assertFalse(dialog.worker.error, dialog.worker.error)
                self.assertTrue((destination / "draft_content.json").is_file())
                draft = json.loads((destination / "draft_content.json").read_text())
                self.assertEqual(draft["canvas_config"]["width"], 64)
                self.assertEqual(draft["fps"], 10)
                from ui.controllers.capcut_export_controller import _ExportDialog
                imported = Path(folder) / "native_import"
                import_dialog = _ExportDialog({"package_source": str(destination)}, imported, gui)
                import_dialog.worker.start()
                import_dialog.exec()
                self.assertFalse(import_dialog.worker.error, import_dialog.worker.error)
                self.assertTrue((imported / "draft_content.json").is_file())
            finally:
                gui.deleteLater()


if __name__ == "__main__":
    unittest.main()
