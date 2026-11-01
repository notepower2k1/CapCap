"""Regression tests for one timing model across preview, MP4 and CapCut."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from app.services.time_warp_service import TimeWarpService as T
from app.services.time_warp_plan import compile_plan


OVERLAPS = [
    {"id": "a", "type": "slow", "media_start": 2.688, "media_end": 5.028, "duration": 2.93, "speed": .444},
    {"id": "b", "type": "slow", "media_start": 4.968, "media_end": 5.568, "duration": .49, "speed": .55},
    {"id": "c", "type": "slow", "media_start": 5.508, "media_end": 6.408, "duration": 1.65, "speed": .353},
]


class TimeWarpPlanTests(unittest.TestCase):
    def test_long_ffmpeg_graph_uses_file_and_cleans_it_on_success_or_error(self):
        from unittest.mock import patch
        import video_processor
        graph = "[0:v]null[v];" * 3000
        for fail in (False, True):
            files = []
            def execute(command, *_):
                self.assertNotIn("-filter_complex", command)
                path = Path(command[command.index("-filter_complex_script") + 1])
                self.assertEqual(path.read_text(encoding="utf-8"), graph)
                files.append(path)
                if fail:
                    raise RuntimeError("Encoder failed")
                return "ok"
            with patch.object(video_processor, "_run_ffmpeg_command_with_progress_impl", side_effect=execute):
                command = ["ffmpeg", "-i", "source.mp4", "-filter_complex", graph, "out.mp4"]
                if fail:
                    with self.assertRaises(RuntimeError):
                        video_processor._run_ffmpeg_command_with_progress(command)
                else:
                    self.assertEqual(video_processor._run_ffmpeg_command_with_progress(command), "ok")
            self.assertFalse(files[0].exists())
            self.assertIn("-filter_complex", command)  # caller list was not modified

    def test_legacy_overlap_and_rounded_speeds_have_continuous_inverse(self):
        original = copy.deepcopy(OVERLAPS)
        normalized = T.normalize_warps(OVERLAPS)
        self.assertEqual(original, OVERLAPS)
        self.assertEqual(normalized, T.normalize_warps(normalized))
        self.assertEqual(normalized[1]["media_start"], 5.028)
        self.assertEqual(normalized[2]["media_start"], 5.568)
        for span in compile_plan(OVERLAPS):
            self.assertAlmostEqual(T.timeline_to_media_time(span.timeline_end, OVERLAPS), span.source_end, places=10)
            a = T.timeline_to_media_time(span.timeline_end - .000001, OVERLAPS)
            b = T.timeline_to_media_time(span.timeline_end + .000001, OVERLAPS)
            self.assertGreaterEqual(b, a)
            self.assertLess(b - a, .000003)
        for i in range(1401):
            t = i / 100.
            m = T.timeline_to_media_time(t, OVERLAPS)
            self.assertAlmostEqual(T.media_to_timeline_time(m, OVERLAPS), t, places=9)
        self.assertAlmostEqual(T.media_to_timeline_time(10, OVERLAPS), 10 + sum(w["duration"] for w in OVERLAPS))

    def test_nested_equal_end_and_freeze_inside_slow_preserve_all_extra(self):
        warps = [
            {"id": "outer", "type": "slow", "media_start": 1., "media_end": 8., "duration": 2.},
            {"id": "inner", "type": "slow", "media_start": 2., "media_end": 4., "duration": 1.},
            {"id": "same", "type": "slow", "media_start": 3., "media_end": 4., "duration": .6},
            {"id": "freeze", "type": "freeze", "time": 5., "duration": .7},
        ]
        parts = T.build_video_parts(10, warps)
        self.assertAlmostEqual(sum(p[2] for p in parts), 14.3)
        self.assertAlmostEqual(sum(p[2] * p[3] for p in parts if not p[4]), 10)
        for a, b in zip(parts, parts[1:]):
            self.assertAlmostEqual(a[0] + (0 if a[4] else a[2] * a[3]), b[0])
            self.assertAlmostEqual(a[1] + a[2], b[1])
        freeze = next(s for s in compile_plan(warps) if s.type == "freeze")
        self.assertAlmostEqual(T.media_to_timeline_time(5, warps), freeze.timeline_end)
        self.assertAlmostEqual(T.timeline_to_media_time(freeze.timeline_start + .1, warps), 5)

    def test_window_uses_same_plan_without_rounding_or_extra_frames(self):
        start, length = 6.123456, 5.678912
        source, duration, local = T.slice_time_warps_for_window(OVERLAPS, start, length)
        self.assertAlmostEqual(source, T.timeline_to_media_time(start, OVERLAPS))
        self.assertAlmostEqual(sum(p[2] for p in T.build_video_parts(duration, local)), length)
        for i in range(101):
            t = length * i / 100
            self.assertAlmostEqual(source + T.timeline_to_media_time(t, local),
                                   T.timeline_to_media_time(start + t, OVERLAPS), places=8)
        freeze = [{"type": "freeze", "time": 3, "duration": 2}]
        source, duration, local = T.slice_time_warps_for_window(freeze, 3.5, .75)
        self.assertEqual(duration, 0)
        self.assertAlmostEqual(sum(p[2] for p in T.build_video_parts(duration, local)), .75)

    def test_repeated_fit_replaces_extension_and_keeps_full_speed_precision(self):
        segments = [{"start": 0., "end": .7}, {"start": .64, "end": 2.}]
        old, _, _ = T.apply_segment_extension(segments, 0, .43, warp_type="slow")
        new, _, _ = T.apply_segment_extension(segments, 0, .27, warp_type="slow")
        self.assertEqual(new["media_start"], 0)
        self.assertEqual(new["media_end"], .7)
        self.assertAlmostEqual(new["speed"], .7 / 1.4, places=12)
        self.assertAlmostEqual(new["duration"], .7)
        self.assertAlmostEqual(T.media_to_timeline_time(2, [new]), 2.7)


@unittest.skipUnless((ROOT / "bin/ffmpeg/ffmpeg.exe").is_file(), "Bundled FFmpeg required")
class NativeTimeWarpRenderTests(unittest.TestCase):
    def run_ffmpeg(self, *args):
        subprocess.run([str(ROOT / "bin/ffmpeg/ffmpeg.exe"), "-v", "error", "-y", *args],
                       capture_output=True, check=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_many_fractional_warps_match_capcut_duration_and_keep_tail_audio(self):
        from app.capcut.draft_export import video_parts
        import wave
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            source, output, decoded = folder / "source.mp4", folder / "out.mkv", folder / "out.wav"
            self.run_ffmpeg("-f", "lavfi", "-i", "testsrc2=size=64x48:rate=30:duration=8",
                            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
                            "-c:v", "libx264", "-c:a", "aac", str(source))
            warps = [{"type": "slow", "media_start": i * .17, "media_end": i * .17 + .23,
                      "duration": .137, "speed": .626} for i in range(35)]
            warps.extend([{"type": "freeze", "time": 6.5, "duration": .41},
                          {"type": "freeze", "time": 8., "duration": .23}])
            graph, v, a = T.build_ffmpeg_timewarp_filtergraph("0:v", warps, 8, fps=30, audio_stream="0:a")
            self.run_ffmpeg("-i", str(source), "-filter_complex", graph, "-map", f"[{v}]", "-map", f"[{a}]",
                            "-c:v", "libx264", "-c:a", "pcm_s16le", str(output))
            data = json.loads(subprocess.run([str(ROOT / "bin/ffmpeg/ffprobe.exe"), "-v", "error", "-show_format",
                                              "-show_streams", "-of", "json", str(output)], capture_output=True, check=True).stdout)
            expected = sum(p[2] for p in video_parts(8, warps))
            self.assertLessEqual(abs(float(data["format"]["duration"]) - expected), 1 / 30 + .002)
            self.run_ffmpeg("-i", str(output), "-vn", "-ac", "1", str(decoded))
            with wave.open(str(decoded), "rb") as audio:
                rate = audio.getframerate()
                samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16)
            self.assertLess(abs(len(samples) / rate - expected), .01)
            # Last source second remains audible, followed by the final hold's silence.
            tail = samples[int((expected - .7) * rate):int((expected - .3) * rate)]
            self.assertGreater(np.sqrt(np.mean(tail.astype(float) ** 2)), 100)
            self.assertLess(np.max(np.abs(samples[-int(.15 * rate):])), 5)


if __name__ == "__main__":
    unittest.main()
