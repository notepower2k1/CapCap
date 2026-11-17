import sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
for folder in (root, root / "app", root / "ui"):
    sys.path.insert(0, str(folder))
from ui.utils.subtitle_effects import preview_effects


def test_fade_uses_cue_start_and_end_with_export_fade_durations():
    cue = dict(start=2., end=5., text="A subtitle")
    style = dict(animation="Fade Out", animation_duration=1.)
    assert preview_effects(cue, style, 2000)["animation_opacity"] == 0
    assert preview_effects(cue, style, 2500)["animation_opacity"] == 1
    assert abs(preview_effects(cue, style, 4600)["animation_opacity"] - .5) < .001
    assert preview_effects(cue, style, 5000)["animation_opacity"] == 0


def test_karaoke_uses_each_tracks_own_cue_timing():
    style = dict(animation="Word Highlight Karaoke")
    assert preview_effects(dict(start=0, end=4, text="one two three four"), style, 2500)["karaoke_word_index"] == 2
    assert preview_effects(dict(start=2, end=4, text="one two three four"), style, 2500)["karaoke_word_index"] == 1
