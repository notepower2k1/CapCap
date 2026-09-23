import os
import sys
import unittest
import numpy as np
import cv2

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

import ocr_processor


class TestOcrEnhancements(unittest.TestCase):
    def test_sanitize_ocr_line_repairs_radicals(self):
        # '亻尔' -> '你'
        self.assertEqual(ocr_processor._sanitize_ocr_line("临走之前再求亻尔个事"), "临走之前再求你个事")
        # '亻故' -> '做'
        self.assertEqual(ocr_processor._sanitize_ocr_line("好人亻故到底"), "好人做到底")
        # '彳亍' (2 chars) -> '行' (1 char)
        self.assertEqual(ocr_processor._sanitize_ocr_line("彳亍行行行行"), "行行行行行")

    def test_sanitize_ocr_line_strips_chinese_punctuation(self):
        self.assertEqual(ocr_processor._sanitize_ocr_line("、 ． 我今天挺谢谢你的"), "我今天挺谢谢你的")
        self.assertEqual(ocr_processor._sanitize_ocr_line("好事这是好事 ，"), "好事这是好事")
        self.assertEqual(ocr_processor._sanitize_ocr_line("——这理由有点牵强啊……"), "这理由有点牵强啊")

    def test_dim_background_for_ocr(self):
        # Create an image with bright white text in center on a gray background
        h, w = 100, 400
        img = np.full((h, w, 3), 100, dtype=np.uint8)  # gray background
        # Add white subtitle box
        img[30:70, 50:350] = (255, 255, 255)  # white text

        dimmed = ocr_processor._dim_background_for_ocr(img)
        self.assertIsNotNone(dimmed)
        self.assertEqual(dimmed.shape, img.shape)
        
        # White text area should still be bright (close to 255)
        text_pixel = dimmed[50, 100]
        self.assertGreater(text_pixel[0], 240)
        
        # Background pixel should be significantly dimmed (100 * 0.20 = 20)
        bg_pixel = dimmed[10, 10]
        self.assertLess(bg_pixel[0], 40)

    def test_dim_background_for_ocr_handles_edge_cases(self):
        self.assertIsNone(ocr_processor._dim_background_for_ocr(None))
        empty = np.array([])
        self.assertEqual(ocr_processor._dim_background_for_ocr(empty).size, 0)
        
        # All black frame -> ratio < 0.0005 -> returned untouched
        black = np.zeros((100, 100, 3), dtype=np.uint8)
        self.assertTrue(np.array_equal(ocr_processor._dim_background_for_ocr(black), black))

    def test_merge_adjacent_handles_fuzzy_and_exact(self):
        segments = [
            {"start": 212.75, "end": 213.25, "text": "临走之前再求你个事"},
            {"start": 213.25, "end": 214.25, "text": "临走之前再求亻尔个事"},  # will be sanitized to '你'
            {"start": 214.25, "end": 215.75, "text": "临走之前再求你个事"},
            {"start": 220.00, "end": 222.00, "text": "完全不同的一句话"},
        ]
        merged = ocr_processor._merge_adjacent(segments, max_gap=0.5)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["text"], "临走之前再求你个事")
        self.assertAlmostEqual(merged[0]["start"], 212.75)
        self.assertAlmostEqual(merged[0]["end"], 215.75)
        self.assertEqual(merged[1]["text"], "完全不同的一句话")

    def test_merge_adjacent_substring_flicker(self):
        # When OCR drops a trailing or leading character on one frame
        segments = [
            {"start": 10.0, "end": 11.0, "text": "你住哪啊"},
            {"start": 11.0, "end": 11.5, "text": "你住"},  # short substring flicker
            {"start": 15.0, "end": 16.0, "text": "拜拜"},
        ]
        merged = ocr_processor._merge_adjacent(segments, max_gap=0.5)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["text"], "你住哪啊")
        self.assertAlmostEqual(merged[0]["start"], 10.0)
        self.assertAlmostEqual(merged[0]["end"], 11.5)

    def test_crop_hash_and_hamming_distance(self):
        # Frame A: black background with white text block at x=50..100
        img_a = np.zeros((100, 400, 3), dtype=np.uint8)
        img_a[40:60, 50:150] = (255, 255, 255)
        h_a = ocr_processor._crop_hash(img_a)

        # Frame A_identical: same text, but background has subtle noise
        img_a_noisy = img_a.copy()
        img_a_noisy[0:20, 0:20] = (20, 30, 25)  # background noise
        h_a_noisy = ocr_processor._crop_hash(img_a_noisy)

        # Distance should be 0.0 or negligible (< threshold)
        dist_same = ocr_processor._hamming_distance(h_a, h_a_noisy)
        self.assertLess(dist_same, ocr_processor.EXACT_HASH_THRESHOLD)

        # Frame B: completely different text position / shape at x=200..350
        img_b = np.zeros((100, 400, 3), dtype=np.uint8)
        img_b[40:60, 200:350] = (255, 255, 255)
        h_b = ocr_processor._crop_hash(img_b)

        # Distance between different text shapes must exceed threshold
        dist_diff = ocr_processor._hamming_distance(h_a, h_b)
        self.assertGreater(dist_diff, ocr_processor.EXACT_HASH_THRESHOLD)

    def test_crop_hash_edge_cases(self):
        self.assertEqual(ocr_processor._hamming_distance(None, None), 1.0)
        blank_a = np.zeros((10, 10), dtype=bool)
        blank_b = np.zeros((10, 10), dtype=bool)
        self.assertEqual(ocr_processor._hamming_distance(blank_a, blank_b), 0.0)


    def test_merge_adjacent_preserves_character_names_and_single_chars(self):
        # Character name at 37s must not be swallowed by next sentence at 41s or within max_gap
        segments = [
            {"start": 37.12, "end": 37.62, "text": "由嘉郁"},
            {"start": 37.80, "end": 39.50, "text": "由嘉郁你真行啊"},
        ]
        merged = ocr_processor._merge_adjacent(segments, max_gap=0.5)
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["text"], "由嘉郁")
        self.assertEqual(merged[1]["text"], "由嘉郁你真行啊")

        # 1-character utterance must not be swallowed
        segments_single = [
            {"start": 35.12, "end": 35.62, "text": "嗯"},
            {"start": 35.80, "end": 36.50, "text": "嗯好的"},
        ]
        merged_single = ocr_processor._merge_adjacent(segments_single, max_gap=0.5)
        self.assertEqual(len(merged_single), 2)
        self.assertEqual(merged_single[0]["text"], "嗯")
        self.assertEqual(merged_single[1]["text"], "嗯好的")

    def test_is_blank_region_preserves_single_character_strokes(self):
        # 1080p crop with 1 small single character (e.g. 35 bright pixels)
        h, w = 150, 800
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # Put 35 bright pixels resembling a small character stroke
        img[70:75, 400:407] = (220, 220, 220)
        self.assertFalse(ocr_processor._is_blank_region(img))

        # Truly blank frame
        blank = np.zeros((h, w, 3), dtype=np.uint8)
        self.assertTrue(ocr_processor._is_blank_region(blank))


if __name__ == "__main__":
    unittest.main()
