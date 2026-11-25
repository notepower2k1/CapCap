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


def test_legacy_auto_highlights_are_ignored_but_manual_highlights_still_render(tmp_path):
    from app.subtitle_presentation import render_ass, valid_segments

    cue = dict(start=0, end=3, text="important project update", auto_highlights=["important"])
    style = dict(auto_keyword_highlight=True, highlight_mode="Auto", preset_key="highlight")
    assert preview_effects(cue, style, 1000)["highlight_phrases"] == []
    plain_ass = Path(render_ass([cue], style, 720, 1280, tmp_path / "plain.srt"))
    plain_event = next(line for line in plain_ass.read_text(encoding="utf-8-sig").splitlines()
                       if line.startswith("Dialogue:"))
    assert plain_event.endswith(cue["text"])

    cue["manual_highlights"] = ["update"]
    secondary = valid_segments([cue])[0]
    assert "auto_highlights" not in secondary
    assert preview_effects(secondary, style, 1000)["highlight_phrases"] == ["update"]
    manual_ass = Path(render_ass([secondary], dict(style, manual_highlights=[["update"]]),
                                 720, 1280, tmp_path / "manual.srt"))
    manual_event = next(line for line in manual_ass.read_text(encoding="utf-8-sig").splitlines()
                        if line.startswith("Dialogue:"))
    assert r"important project {\c" in manual_event
