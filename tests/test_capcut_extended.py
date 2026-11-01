"""Integration checks for editable styles, envelopes, cuts and keyframes."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

import test_capcut_draft_export as fixtures
from app.capcut.draft_clips import source_clip_parts
from app.capcut.draft_keyframes import visual_keyframes, slice_keyframes
from app.capcut.draft_package import install_package


class ClipAndKeyframeTests(unittest.TestCase):
    def test_cut_window_includes_partial_hold_slow_and_clip_speed(self):
        parts = [(0.,0.,1.,1.,False), (1.,1.,.6,1.,True), (1.,1.6,1.,1.,False), (2.,2.6,3.4,2/3.4,False), (4.,6.,2.,1.,False)]
        result = source_clip_parts(parts, [{"track_start": 3., "source_start": 1.2, "duration": 2.4, "speed": 2.}])
        self.assertEqual(len(result), 3)
        self.assertAlmostEqual(sum(p[0][2] for p in result), 2.4)
        self.assertTrue(result[0][0][4])
        self.assertAlmostEqual(result[0][0][2], .2)
        self.assertAlmostEqual(result[-1][0][3], 2/3.4*2)
        with self.assertRaises(ValueError):
            source_clip_parts(parts, [{"duration": 20}])

    def test_trimmed_eased_keys_retain_local_clock_and_geometry(self):
        layer = {"type": "text", "transform": {"x": .2, "y": .5, "keyframes": [
            {"property": "x", "keyframes": [{"time": 0, "value": .2}, {"time": 4, "value": .8, "easing": "ease_in"}]}]},
            "_keyframe_offset": 1., "_keyframe_rate": 2.}
        before = copy.deepcopy(layer)
        groups = visual_keyframes(layer, (100, 100), {}, 1., lambda: "id")
        x = next(k for k in groups if k["property_type"] == "KFTypePositionX")["keyframe_list"]
        self.assertAlmostEqual(x[0]["values"][0], 2*(.2+.6*(1/4)**2)-1)
        self.assertAlmostEqual(x[-1]["values"][0], 2*(.2+.6*(3/4)**2)-1)
        self.assertEqual(x[-1]["time_offset"], 1000000)
        self.assertGreater(len(x), 2)
        sliced = slice_keyframes(groups, .25, .5, lambda: "new")
        self.assertEqual(sliced[0]["keyframe_list"][-1]["time_offset"], 500000)
        self.assertEqual(before, layer)


@unittest.skipUnless(fixtures.FFMPEG.is_file() and fixtures.FFPROBE.is_file(), "Bundled FFmpeg required")
class ExtendedExportTests(unittest.TestCase):
    setUpClass = classmethod(fixtures.DraftExportTests.setUpClass.__func__)
    tearDownClass = classmethod(fixtures.DraftExportTests.tearDownClass.__func__)
    setUp = fixtures.DraftExportTests.setUp
    export = fixtures.DraftExportTests.export

    def test_text_styles_and_font_rebound_on_destination(self):
        cue = self.snapshot["timeline"]["tracks"][2]["layers"][0]
        cue.update(font_name="Test Family", font_bold=True, font_italic=True, font_underline=True,
                   outline_width=3, outline_color="#123456", background_color="#102030", background_opacity=.4,
                   shadow_depth=3, shadow_color="#223344", shadow_alpha=.6, alignment="right")
        with patch("app.capcut.draft_fonts.font_path", return_value="C:/old/fonts/test.ttf"):
            destination, draft, _ = self.export()
        material = draft["materials"]["texts"][0]
        style = json.loads(material["content"])["styles"][0]
        self.assertTrue(all(style[k] for k in ("bold", "italic", "underline")))
        self.assertEqual(material["check_flag"], 63)
        self.assertEqual(material["alignment"], 2)
        self.assertEqual(material["background_alpha"], .4)
        self.assertEqual(style["shadows"][0]["alpha"], .6)
        self.assertEqual(style["font"]["path"], "C:/old/fonts/test.ttf")
        imported = destination.parent / (destination.name+"_moved")
        self.addCleanup(lambda: __import__('shutil').rmtree(imported, ignore_errors=True))
        with patch("app.capcut.draft_fonts.font_path", return_value="E:/new/fonts/test.ttf"):
            install_package(destination, imported)
        moved = json.loads((imported/"draft_content.json").read_text())
        for material in moved["materials"]["texts"]:
            self.assertEqual(material["font_path"], "E:/new/fonts/test.ttf")
            self.assertEqual(json.loads(material["content"])["styles"][0]["font"]["path"], "E:/new/fonts/test.ttf")

    def test_track_fade_does_not_restart_at_cue_or_loop_boundaries(self):
        self.snapshot["timeline"]["tracks"][2]["metadata"] = {"_fade_in": 4., "_fade_out": 2.}
        self.snapshot["music"] = [{"id": "music", "path": str(self.voice), "start": 0, "end": 18, "volume": 30,
                                    "loop": True, "track_name": "Music", "track_metadata": {"_fade_in": 10., "_fade_out": 2.}}]
        _, draft, _ = self.export()
        voice = next(t for t in draft["tracks"] if t["name"] == "TS1 Audio")["segments"]
        keys = next(k for k in voice[0]["common_keyframes"] if k["property_type"] == "KFTypeVolume")["keyframe_list"]
        self.assertAlmostEqual(keys[0]["values"][0], .5)  # cue at project 2s, not fade from zero
        music = next(t for t in draft["tracks"] if t["name"] == "Music")["segments"]
        next_start = next(k for k in music[1]["common_keyframes"] if k["property_type"] == "KFTypeVolume")["keyframe_list"][0]
        self.assertAlmostEqual(next_start["values"][0], .24)  # 8s / 10s * 30%

    def test_native_layer_fade_and_pan_produce_audible_left_only_audio(self):
        self.snapshot["timeline"]["tracks"].append({"name": "Extra", "type": "audio", "layers": [
            {"type": "audio", "source": str(self.voice), "start": 1, "end": 4, "pan": -1., "fade_in": .5, "fade_out": .75}]})
        _, draft, result = self.export()
        segment = next(t for t in draft["tracks"] if t["name"] == "Extra Audio")["segments"][0]
        fade = next(m for m in draft["materials"]["audio_fades"] if m["id"] in segment["extra_material_refs"])
        self.assertEqual((fade["fade_in_duration"], fade["fade_out_duration"]), (500000, 750000))
        audio = next(m for m in draft["materials"]["audios"] if m["id"] == segment["material_id"])
        samples, _ = sf.read(audio["path"], always_2d=True)
        self.assertEqual(samples.shape[1], 2)
        self.assertGreater(np.max(np.abs(samples[:,0])), .01)
        self.assertEqual(np.max(np.abs(samples[:,1])), 0)
        self.assertTrue(any("pan is preserved" in w for w in result["warnings"]))

    def test_source_video_and_audio_clips_keep_holds_speeds_and_region_slices(self):
        tracks = self.snapshot["timeline"]["tracks"]
        for track, kind in ((tracks[0], "video"), (tracks[1], "audio")):
            track["layers"] = [{"id": kind, "type": kind, "source": str(self.video), "start": 0, "end": 8}]
            track["clips"] = [{"layer_id": kind, "track_start": 0, "source_start": 1.2, "duration": 2.4, "speed": 2.},
                              {"layer_id": kind, "track_start": 4, "source_start": 6, "duration": 1, "speed": 1.}]
        _, draft, _ = self.export()
        video = next(t for t in draft["tracks"] if t["name"] == "V1 Video")["segments"]
        self.assertEqual(len(video), 4)
        self.assertEqual(video[0]["target_timerange"], {"start": 0, "duration": 200000})
        self.assertAlmostEqual(video[2]["speed"], 2/3.4*2)
        self.assertEqual(video[-1]["source_timerange"], {"start": 4000000, "duration": 1000000})
        audio = next(t for t in draft["tracks"] if t["name"] == "A1 Original")["segments"]
        self.assertEqual(len(audio), 3)  # no audio over source hold
        self.assertEqual(audio[0]["target_timerange"]["start"], 200000)
        regions = next(t for t in draft["tracks"] if t["name"] == "B1 Regions")["segments"]
        self.assertEqual([s["target_timerange"] for s in regions], [s["target_timerange"] for s in video])

    def test_video_pan_with_volume_keys_does_not_duplicate_embedded_audio(self):
        self.snapshot["timeline"]["tracks"].append({"name": "Overlay", "type": "video", "layers": [
            {"type": "video", "source": str(self.video), "start": 1, "end": 3, "pan": 1., "volume": .5,
             "transform": {"keyframes": [{"property": "volume", "keyframes": [
                 {"time": 0, "value": .5}, {"time": 2, "value": 1.}]}]}}]})
        _, draft, _ = self.export()
        video = next(t for t in draft["tracks"] if t["name"] == "Overlay")["segments"][0]
        self.assertEqual(video["volume"], 0)
        self.assertFalse(any(k["property_type"] == "KFTypeVolume" for k in video["common_keyframes"]))
        audio = next(t for t in draft["tracks"] if t["name"] == "Overlay Audio")["segments"][0]
        keys = next(k for k in audio["common_keyframes"] if k["property_type"] == "KFTypeVolume")["keyframe_list"]
        self.assertEqual([k["values"][0] for k in keys], [.5, 1.])
        material = next(m for m in draft["materials"]["audios"] if m["id"] == audio["material_id"])
        samples, _ = sf.read(material["path"], always_2d=True)
        self.assertEqual(np.max(np.abs(samples[:,0])), 0)
        self.assertGreater(np.max(np.abs(samples[:,1])), .01)


if __name__ == "__main__":
    unittest.main()
