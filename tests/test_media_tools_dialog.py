#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_media_tools_dialog.py

Unit and regression test suite for MediaToolsDialog:
- Pure command generators and time formatters: format_hms, parse_hms.
- FFmpeg command builders: split, merge, extract, and mux.
- Concat demuxer list writer with escaping.
- GUI dialog lifecycle, tabs, and mode switching.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root, app, and ui are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
UI_DIR = os.path.join(PROJECT_ROOT, "ui")
for p in (PROJECT_ROOT, APP_DIR, UI_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from PySide6.QtWidgets import QApplication

from ui.views.media_tools_dialog import (
    MediaToolsDialog,
    build_extract_ffmpeg_tasks,
    build_merge_ffmpeg_args,
    build_mux_ffmpeg_args,
    build_split_ffmpeg_args,
    format_hms,
    parse_hms,
    write_concat_list_file,
)


class TestMediaToolsDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_format_hms(self):
        self.assertEqual(format_hms(0), "00:00:00")
        self.assertEqual(format_hms(65), "00:01:05")
        self.assertEqual(format_hms(3661.4), "01:01:01")
        self.assertEqual(format_hms(86400), "24:00:00")
        self.assertEqual(format_hms(-5), "00:00:00")

    def test_parse_hms(self):
        self.assertAlmostEqual(parse_hms("01:01:01"), 3661.0)
        self.assertAlmostEqual(parse_hms("10:30"), 630.0)
        self.assertAlmostEqual(parse_hms("45"), 45.0)
        self.assertAlmostEqual(parse_hms("45.5"), 45.5)
        self.assertAlmostEqual(parse_hms("00:00:15.25"), 15.25)

        # Invalid cases
        self.assertEqual(parse_hms(""), -1.0)
        self.assertEqual(parse_hms("invalid"), -1.0)
        self.assertEqual(parse_hms("-10:00"), -1.0)
        self.assertEqual(parse_hms("01:02:03:04"), -1.0)

    def test_build_split_ffmpeg_args_duration(self):
        args, out_target = build_split_ffmpeg_args(
            "C:/videos/my_video.mp4",
            "C:/output/parts",
            "duration",
            segment_seconds=300.0,
        )
        self.assertEqual(out_target, "C:/output/parts")
        self.assertIn("-f", args)
        idx_f = args.index("-f")
        self.assertEqual(args[idx_f + 1], "segment")
        self.assertIn("-segment_time", args)
        idx_time = args.index("-segment_time")
        self.assertEqual(args[idx_time + 1], "300.000")
        self.assertIn("-c", args)
        self.assertEqual(args[args.index("-c") + 1], "copy")
        self.assertTrue(args[-1].endswith("my_video_part%03d.mp4"))

    def test_build_split_ffmpeg_args_parts(self):
        args, out_target = build_split_ffmpeg_args(
            "C:/videos/my_video.mkv",
            "C:/output/parts",
            "parts",
            num_parts=4,
            total_duration=1200.0,
        )
        self.assertEqual(out_target, "C:/output/parts")
        idx_time = args.index("-segment_time")
        self.assertEqual(args[idx_time + 1], "300.000")
        self.assertTrue(args[-1].endswith("my_video_part%03d.mkv"))

    def test_build_split_ffmpeg_args_trim(self):
        args, out_file = build_split_ffmpeg_args(
            "C:/videos/clip.mp4",
            "C:/output",
            "mode" if False else "trim",
            trim_start_s=60.0,
            trim_end_s=180.0,
        )
        self.assertIn("-ss", args)
        self.assertEqual(args[args.index("-ss") + 1], "60.000")
        self.assertIn("-to", args)
        self.assertEqual(args[args.index("-to") + 1], "180.000")
        self.assertIn("-avoid_negative_ts", args)
        self.assertTrue("trim_000100_000300" in out_file)
        self.assertEqual(args[-1], out_file)

    def test_write_concat_list_file_and_build_merge_args(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            list_file = f.name

        try:
            video_files = [
                os.path.abspath("video1.mp4"),
                os.path.abspath("video_with_'quote.mp4"),
            ]
            write_concat_list_file(video_files, list_file)

            with open(list_file, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("file '", content)
            self.assertIn("video_with_'\\''quote.mp4'", content)

            # 1. Stream copy mode
            args_copy = build_merge_ffmpeg_args(video_files, "out_merged.mp4", list_file, reencode=False)
            self.assertIn("-c", args_copy)
            self.assertEqual(args_copy[args_copy.index("-c") + 1], "copy")
            self.assertEqual(args_copy[-1], "out_merged.mp4")

            # 2. Re-encode mode
            args_reencode = build_merge_ffmpeg_args(video_files, "out_merged.mp4", list_file, reencode=True)
            self.assertIn("scale=1920:1080", " ".join(args_reencode))
            self.assertIn("libx264", args_reencode)
            self.assertIn("aac", args_reencode)
        finally:
            if os.path.exists(list_file):
                os.remove(list_file)

    def test_build_extract_ffmpeg_tasks(self):
        # 1. Both audio and video
        tasks_both = build_extract_ffmpeg_tasks("C:/in/sample.mp4", "C:/out", "both", "mp3")
        self.assertEqual(len(tasks_both), 2)
        label_a, args_a, out_a = tasks_both[0]
        label_v, args_v, out_v = tasks_both[1]
        self.assertTrue(out_a.endswith(".mp3"))
        self.assertIn("libmp3lame", args_a)
        self.assertTrue(out_v.endswith("_no_audio.mp4"))
        self.assertIn("-an", args_v)

        # 2. Audio only (WAV)
        tasks_wav = build_extract_ffmpeg_tasks("C:/in/sample.mp4", "C:/out", "audio_only", "wav")
        self.assertEqual(len(tasks_wav), 1)
        self.assertTrue(tasks_wav[0][2].endswith(".wav"))
        self.assertIn("pcm_s16le", tasks_wav[0][1])

        # 3. Audio only (M4A)
        tasks_m4a = build_extract_ffmpeg_tasks("C:/in/sample.mp4", "C:/out", "audio_only", "m4a")
        self.assertEqual(len(tasks_m4a), 1)
        self.assertTrue(tasks_m4a[0][2].endswith(".m4a"))
        self.assertIn("aac", tasks_m4a[0][1])

        # 4. Video only
        tasks_video = build_extract_ffmpeg_tasks("C:/in/sample.mp4", "C:/out", "video_only", "mp3")
        self.assertEqual(len(tasks_video), 1)
        self.assertTrue(tasks_video[0][2].endswith("_no_audio.mp4"))

    def test_build_mux_ffmpeg_args(self):
        args_short = build_mux_ffmpeg_args("v.mp4", "a.wav", "out.mp4", shortest=True)
        self.assertIn("-shortest", args_short)
        self.assertIn("-map", args_short)
        self.assertEqual(args_short[-1], "out.mp4")

        args_noshort = build_mux_ffmpeg_args("v.mp4", "a.wav", "out.mp4", shortest=False)
        self.assertNotIn("-shortest", args_noshort)

    def test_dialog_tabs_and_split_modes(self):
        dlg = MediaToolsDialog()
        dlg.show()
        QApplication.processEvents()

        # Alignment and sizing assertions
        self.assertEqual(dlg.split_mins_spin.geometry().x(), dlg.split_parts_spin.geometry().x())
        self.assertEqual(dlg.split_parts_spin.geometry().x(), dlg.trim_start_edit.geometry().x())
        self.assertEqual(dlg.split_secs_spin.geometry().x(), dlg.trim_end_edit.geometry().x())
        self.assertEqual(dlg.split_mins_spin.height(), 34)
        self.assertEqual(dlg.split_parts_spin.height(), 34)
        self.assertEqual(dlg.trim_start_edit.height(), 34)

        # No vertical overlap assertions
        self.assertLess(dlg.split_mins_spin.geometry().bottom(), dlg.rb_parts.geometry().top())
        self.assertLess(dlg.split_parts_spin.geometry().bottom(), dlg.rb_trim.geometry().top())

        dlg.close()
        dlg.deleteLater()
        QApplication.processEvents()

    def test_media_tools_dialog_gui_lifecycle(self):
        dialog = MediaToolsDialog()
        dialog.show()
        self.app.processEvents()

        self.assertEqual(dialog.tabs.count(), 4)

        # Tab navigation
        dialog.tabs.setCurrentIndex(0)
        self.assertEqual(dialog.tabs.currentIndex(), 0)

        # Test Split Tab options
        dialog.rb_duration.setChecked(True)
        dialog.split_mins_spin.setValue(15)
        self.assertEqual(dialog.split_mins_spin.value(), 15)

        dialog.rb_parts.setChecked(True)
        dialog.split_parts_spin.setValue(5)
        self.assertEqual(dialog.split_parts_spin.value(), 5)

        dialog.rb_trim.setChecked(True)
        dialog.trim_start_edit.setText("00:01:00")
        dialog.trim_end_edit.setText("00:03:00")

        # Verify spinbox and input sizing have generous room
        self.assertGreaterEqual(dialog.split_mins_spin.width(), 96)
        self.assertGreaterEqual(dialog.split_secs_spin.width(), 96)
        self.assertGreaterEqual(dialog.split_parts_spin.width(), 96)
        self.assertGreaterEqual(dialog.trim_start_edit.width(), 110)
        self.assertGreaterEqual(dialog.split_mins_spin.lineEdit().height(), 20)
        self.assertGreaterEqual(dialog.split_mins_spin.lineEdit().width(), 45)

        # Test Merge Tab
        dialog.tabs.setCurrentIndex(1)
        dialog.merge_list.addItem("C:/test/v1.mp4")
        dialog.merge_list.addItem("C:/test/v2.mp4")
        self.assertEqual(dialog.merge_list.count(), 2)
        dialog.merge_list.setCurrentRow(1)
        dialog._on_merge_move_up()
        self.assertEqual(dialog.merge_list.item(0).text(), "C:/test/v2.mp4")

        # Test Extract Tab
        dialog.tabs.setCurrentIndex(2)
        dialog.extract_mode_combo.setCurrentIndex(1)  # audio_only
        self.assertTrue(dialog.extract_fmt_combo.isEnabled())
        dialog.extract_mode_combo.setCurrentIndex(2)  # video_only
        self.assertFalse(dialog.extract_fmt_combo.isEnabled())

        # Test Mux Tab
        dialog.tabs.setCurrentIndex(3)
        dialog.mux_shortest_cb.setChecked(False)
        self.assertFalse(dialog.mux_shortest_cb.isChecked())

        # Clean teardown
        dialog.close()
        dialog.deleteLater()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
