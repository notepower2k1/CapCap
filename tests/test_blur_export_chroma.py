"""Exercise actual FFmpeg pixel output, including NV12 from hardware decoding."""
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from video_processor import _build_blur_filter_chain, _build_mask_filter_chain

FFMPEG = ROOT / "bin/ffmpeg/ffmpeg.exe"


@unittest.skipUnless(FFMPEG.is_file(), "Bundled FFmpeg required")
class TestBlurExportChroma(unittest.TestCase):
    def render(self, builder, pixel_format, regions):
        chain = builder(regions, 1024, 576)
        if builder is _build_mask_filter_chain:
            graph = (f"[0:v]format={pixel_format}[input];" + chain.replace("[0:v]", "[input]", 1)
                     + f";[m{len(regions)-1}]null[out]")
        else:
            graph = f"[0:v]format={pixel_format}," + chain + "[out]"
        result = subprocess.run([
            str(FFMPEG), "-v", "error", "-f", "lavfi", "-i",
            "color=c=gray:s=1024x576:r=1", "-filter_complex", graph,
            "-map", "[out]", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
        ], capture_output=True, timeout=20, check=True)
        self.assertEqual(len(result.stdout), 1024 * 576 * 3)
        return result.stdout

    def test_nv12_blur_matches_planar_without_green_half(self):
        for builder, mode in [(_build_blur_filter_chain, "blur"), (_build_mask_filter_chain, "blur")]:
            for width in [.4, .399]:
                with self.subTest(builder=builder.__name__, width=width):
                    regions = [dict(x=.3, y=.807935, width=width, height=.1, mode=mode)]
                    planar = self.render(builder, "yuv420p", regions)
                    nv12 = self.render(builder, "nv12", regions)
                    self.assertEqual(nv12, planar)
                    for x in [320, 510, 650, 700]:
                        offset = (490 * 1024 + x) * 3
                        r, g, b = nv12[offset:offset+3]
                        self.assertLessEqual(max(r, g, b) - min(r, g, b), 2)

    def test_multiple_blur_regions_with_timing(self):
        regions = [dict(x=.3, y=.807935, width=.4, height=.1, start=0, end=2),
                   dict(x=.1, y=.1, width=.2, height=.15, start=0, end=2)]
        self.assertEqual(self.render(_build_blur_filter_chain, "nv12", regions),
                         self.render(_build_blur_filter_chain, "yuv420p", regions))
