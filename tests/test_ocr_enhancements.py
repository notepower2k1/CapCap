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


if __name__ == "__main__":
    unittest.main()
