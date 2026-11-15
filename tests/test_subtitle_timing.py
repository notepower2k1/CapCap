"""Completed-run timing survives editor edits and project serialization."""
import copy
import sys
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(folder))
from app.subtitle_timing import capture, restored, baseline, KEY
from app.core.models.segment import Segment
from app.subtitle_presentation import valid_segments, signature


class SubtitleTimingTests(unittest.TestCase):
    def test_snapshot_restores_exact_completed_window_after_manual_move(self):
        warps = [dict(type="freeze", time=.4, duration=1.)]
        rows = [dict(start=0., end=1.8, text="Completed")]
        capture(rows, warps)
        rows[0].update(start=.2, end=.5, text="Edited text")
        self.assertEqual(restored(rows[0], warps), (0., 1.8))
        self.assertEqual(rows[0]["text"], "Edited text")
        self.assertEqual(restored(rows[0], []), (0., .8))

    def test_model_and_secondary_storage_preserve_checkpoint(self):
        row = dict(start=1., end=2., text="Text")
        capture([row])
        for model in (Segment.from_dict(row), Segment.from_transcript_dict(row, 1)):
            model = Segment.from_dict(model.to_dict())
            for flat in (model.to_subtitle_dict(), model.to_original_subtitle_dict()):
                self.assertEqual(restored(flat), (1., 2.))
        stored = valid_segments([row])[0]
        self.assertIsNot(stored[KEY], row[KEY])
        self.assertEqual(restored(stored), (1., 2.))

    def test_timing_checkpoint_does_not_invalidate_translation_signature(self):
        rows = [dict(start=1., end=2., text="Text")]
        previous = signature(rows, "vi")
        capture(rows)
        self.assertEqual(signature(rows, "vi"), previous)
        newer = copy.deepcopy(rows)
        newer[0].update(start=3., end=4.)
        capture(newer)
        self.assertEqual(restored(newer[0]), (3., 4.))
        self.assertEqual(restored(rows[0]), (1., 2.))

    def test_missing_or_invalid_checkpoint_is_not_guessed(self):
        self.assertIsNone(restored(dict(start=1., end=2.)))
        self.assertIsNone(baseline({KEY: dict(start=float("nan"), end=2., media_start=1., media_end=2.)}))
        self.assertIsNone(baseline({KEY: dict(start=2., end=1., media_start=1., media_end=2.)}))


if __name__ == "__main__":
    unittest.main()
