"""Cue-timed effects shared by single and bilingual Qt previews."""
def preview_effects(segment, style, position_ms):
    segment = segment or {}
    animation = str(style.get("animation", "Static")).strip().lower()
    duration = max(.01, float(style.get("animation_duration", .22)))
    start = float(segment.get("start", 0) or 0)
    end = max(start + .01, float(segment.get("end", start + .01)))
    elapsed = max(0., position_ms / 1000 - start)
    remaining = max(0., end - position_ms / 1000)
    progress = min(1., elapsed / duration)
    if animation == "fade out":
        progress = min(1., max(0., 1 - remaining / duration))
    opacity = 1.
    fade = {"fade in": (.8, .2), "fade out": (.2, .8),
            "background appear": (.85, .15), "slide up": (.35, .2)}.get(animation)
    if fade:
        opacity = min(1., elapsed / (duration * fade[0]), remaining / (duration * fade[1]))
    text = str(segment.get("text", ""))
    words = text.split()
    karaoke = -1
    if animation == "word highlight karaoke" and words:
        karaoke = min(len(words) - 1, int(max(0., min(.999, elapsed / (end - start))) * len(words)))
    phrases = list(segment.get("manual_highlights") or [])
    color = style.get("highlight_color", "#FFD400")
    if str(color).startswith("&H"):
        color = "#" + color[-2:] + color[-4:-2] + color[-6:-4]
    return dict(animation_style=animation, animation_progress=progress, animation_opacity=opacity,
                highlight_color=color, highlight_phrases=phrases, karaoke_word_index=karaoke)
