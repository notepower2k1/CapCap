import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import ocr_processor


class TestMetadataProbe(unittest.TestCase):
    def test_reads_only_duration_metadata(self):
        with patch.object(ocr_processor.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "1651.2\n", "")) as run, patch.object(ocr_processor, "_open_video") as open_video:
            self.assertEqual(ocr_processor._probe_video_duration("movie.mp4"), 1651.2)
        command = run.call_args.args[0]
        self.assertTrue(command[0].endswith("ffprobe.exe"))
        self.assertIn("format=duration", command)
        self.assertNotIn("null", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 10)
        open_video.assert_not_called()

    def test_missing_probe_and_invalid_metadata_fall_back_without_decoding(self):
        failures = [FileNotFoundError(), subprocess.TimeoutExpired("ffprobe", 10)]
        invalid = [subprocess.CompletedProcess([], code, value, "") for code, value in
                   [(0, "N/A"), (0, "nan"), (0, "inf"), (0, "-5"), (1, "25")]]
        for outcome in failures + invalid:
            with self.subTest(outcome=outcome):
                cap = MagicMock()
                cap.get.side_effect = lambda key: 25 if key == cv2.CAP_PROP_FPS else 41280
                kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
                with patch.object(ocr_processor.subprocess, "run", **kwargs), patch.object(ocr_processor, "_open_video", return_value=cap):
                    self.assertEqual(ocr_processor._probe_video_duration("movie.mp4"), 1651.2)
                cap.release.assert_called_once()
                cap.read.assert_not_called()
                cap.grab.assert_not_called()


class FakeCapture:
    def __init__(self, *, seek_cost=0.02, grab_cost=0.0001, total=400):
        self.position = 0
        self.total = total
        self.now = 0.0
        self.seek_cost = seek_cost
        self.grab_cost = grab_cost
        self.seeks = []

    def set(self, key, value):
        self.position = int(value)
        self.seeks.append(self.position)
        self.now += self.seek_cost
        return True

    def grab(self):
        self.now += self.grab_cost
        if self.position >= self.total:
            return False
        self.position += 1
        return True

    def read(self):
        self.now += 0.001
        if self.position >= self.total:
            return False, None
        frame = np.full((2, 2, 3), self.position % 256, dtype=np.uint8)
        self.position += 1
        return True, frame


class TestFrameReader(unittest.TestCase):
    def test_adapts_to_decode_cost_and_preserves_frame_indices(self):
        for seek_cost, grab_cost, expected in [(0.02, 0.0001, "sequential"), (0.0001, 0.002, "seek")]:
            with self.subTest(expected=expected):
                cap = FakeCapture(seek_cost=seek_cost, grab_cost=grab_cost)
                reader = ocr_processor._OcrFrameReader(cap)
                indices = [round(index * 12.5) for index in range(15)]
                with patch.object(ocr_processor.time, "perf_counter", side_effect=lambda: cap.now):
                    for index in indices:
                        success, frame = reader.read(index)
                        self.assertTrue(success)
                        self.assertEqual(int(frame[0, 0, 0]), index % 256)
                self.assertEqual(reader.strategy, expected)
                if expected == "sequential":
                    self.assertLess(len(cap.seeks), len(indices))

    def test_ranges_repeated_frames_reverse_seeks_sparse_sampling_and_eof(self):
        for mode in ("seek", "sequential", "auto"):
            with self.subTest(mode=mode):
                cap = FakeCapture()
                reader = ocr_processor._OcrFrameReader(cap, mode=mode)
                with patch.object(ocr_processor.time, "perf_counter", side_effect=lambda: cap.now):
                    for index in [27, 27, 28, 39, 180, 8, 10, 399]:
                        success, frame = reader.read(index)
                        self.assertTrue(success)
                        self.assertEqual(int(frame[0, 0, 0]), index % 256)
                    success, frame = reader.read(400)
                    self.assertFalse(success)
                    self.assertIsNone(frame)
                self.assertEqual(cap.seeks[0], 27)
                if mode == "auto":
                    self.assertIn(180, cap.seeks)

    def test_real_video_frames_match_random_seeks(self):
        with tempfile.TemporaryDirectory() as directory:
            video = os.path.join(directory, "frames.avi")
            writer = cv2.VideoWriter(video, cv2.VideoWriter_fourcc(*"MJPG"), 25, (64, 48))
            if not writer.isOpened():
                self.skipTest("MJPG encoder unavailable")
            rng = np.random.default_rng(12)
            try:
                for _ in range(40):
                    writer.write(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8))
            finally:
                writer.release()
            indices = [0, 3, 7, 11, 15, 19, 23, 27, 31, 35, 34, 39]
            reference = cv2.VideoCapture(video)
            expected = []
            try:
                for index in indices:
                    reference.set(cv2.CAP_PROP_POS_FRAMES, index)
                    success, frame = reference.read()
                    self.assertTrue(success)
                    expected.append(frame)
            finally:
                reference.release()
            for mode in ("auto", "sequential"):
                cap = cv2.VideoCapture(video)
                try:
                    reader = ocr_processor._OcrFrameReader(cap, mode=mode)
                    for index, frame in zip(indices, expected):
                        success, actual = reader.read(index)
                        self.assertTrue(success)
                        self.assertTrue(np.array_equal(actual, frame))
                finally:
                    cap.release()


if __name__ == "__main__":
    unittest.main()
