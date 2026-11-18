import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import test_performance_queue as harness
from app.capcut.draft_export import DraftExporter
from app.layers.base import LayerType
from app.video_processor import _build_mask_filter_chain
from app.workflows.export_workflow import ExportWorkflow
from runtime_paths import bin_path
from ui.controllers.capcut_export_controller import capture_snapshot


class MaskImageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        harness.PerformanceQueueTests.setUpClass()

    @classmethod
    def tearDownClass(cls):
        harness.PerformanceQueueTests.tearDownClass()

    def test_image_mask_picker_preview_render_and_capcut(self):
        host = harness.PerformanceQueueTests()
        host.setUp()
        self.addCleanup(host.tearDown)
        source = host.source()
        gui = host.editor(source)
        gui.on_add_timeline_layer("mask")
        mask = next(l for l in gui.timeline._timeline.all_layers() if l.type == LayerType.MASK)
        mask.start, mask.end = 0.2, 0.8
        mask.position_x, mask.position_y, mask.width, mask.height = 0.25, 0.25, 0.5, 0.5
        path = host.root / "mask [alpha], sample.png"
        image = Image.new("RGBA", (20, 20))
        image.paste((255, 0, 0, 255), (0, 0, 10, 20))
        image.save(path)
        with patch.object(harness.main_window.QFileDialog, "getOpenFileName", return_value=(str(path), "")):
            gui.mask_choose_image.click()
        self.assertEqual(mask.source, str(path))
        self.assertEqual(mask.mode, "image")
        gui.video_view.set_mask_regions(gui._current_mask_regions_payload(time_seconds=.4), editable=False)
        overlay = gui.video_view.mask_overlay
        overlay.set_review_mode(True)
        self.assertTrue(overlay._should_show_overlay())
        self.assertFalse(overlay._mask_images[0][1].isNull())

        regions, _, _, _ = ExportWorkflow(str(host.root))._extract_overlay_layers(gui.current_project_state)
        self.assertEqual(len(regions), 1)
        graph = _build_mask_filter_chain(regions, 320, 180)
        ffmpeg = bin_path("ffmpeg", "ffmpeg.exe")
        command = [ffmpeg, "-v", "error", "-i", str(source), "-filter_complex_threads", "1",
                   "-filter_complex", graph, "-map", "[m0]", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
        frames = subprocess.check_output(command)

        def pixel(frame, x):
            offset = ((frame * 180 + 90) * 320 + x) * 3
            return tuple(frames[offset:offset+3])

        self.assertGreater(pixel(0, 100)[2], 220)
        self.assertGreater(pixel(12, 100)[0], 220)
        self.assertGreater(pixel(12, 200)[2], 220)
        self.assertGreater(pixel(23, 100)[2], 220)

        dest = host.root / "draft"
        DraftExporter(ffmpeg=ffmpeg, ffprobe=bin_path("ffmpeg", "ffprobe.exe")).export(capture_snapshot(gui), dest)
        draft = json.loads((dest / "draft_content.json").read_text(encoding="utf-8"))
        photo = next(m for m in draft["materials"]["videos"] if m["name"] == path.name)
        self.assertEqual(photo["type"], "photo")
        self.assertTrue(Path(photo["path"]).is_file())
        gui.mask_remove_image.click()
        self.assertEqual((mask.mode, mask.source), ("solid", ""))
