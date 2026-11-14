"""Last user-selected Audio Mix defaults; project track settings take priority."""
import json
import math

KEY = "audio_mix_defaults"
DEFAULTS = {"original": 50, "tts": 100, "music": 30, "preset": "custom"}
PRESETS = {"original_only": (100, 0), "prefer_original": (80, 20), "balanced": (100, 100), "prefer_dub": (20, 80), "dub_only": (0, 100)}
CONTROLS = {"original": "audio_a1_volume", "tts": "audio_a2_volume", "music": "audio_music_volume"}


def read_mix(settings):
    try:
        data = json.loads(settings.value(KEY, "{}"))
    except (ValueError, TypeError, AttributeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    result = dict(DEFAULTS)
    for key in CONTROLS:
        try:
            value = float(data.get(key, DEFAULTS[key]))
            if math.isfinite(value):
                result[key] = max(0, min(200, round(value)))
        except (TypeError, ValueError):
            pass
    preset = str(data.get("preset", "custom"))
    result["preset"] = preset if PRESETS.get(preset) == (result["original"], result["tts"]) else "custom"
    return result


def save_mix(gui):
    settings = getattr(gui, "settings", None)
    if settings is None:
        return
    data = read_mix(settings)
    for key, prefix in CONTROLS.items():
        slider = getattr(gui, prefix + "_slider", None)
        if slider is not None:
            data[key] = slider.value()
    combo = getattr(gui, "audio_mix_preset_combo", None)
    data["preset"] = combo.currentData() if combo is not None else "custom"
    settings.setValue(KEY, json.dumps(data))


def restore_mix(gui):
    data = read_mix(getattr(gui, "settings", None))
    for key, prefix in CONTROLS.items():
        slider = getattr(gui, prefix + "_slider", None)
        if slider is not None:
            blocked = slider.blockSignals(True)
            slider.setValue(data[key])
            slider.blockSignals(blocked)
        label = getattr(gui, prefix + "_label", None)
        if label is not None:
            label.setText(f"{data[key]}%")
    combo = getattr(gui, "audio_mix_preset_combo", None)
    if combo is not None:
        blocked = combo.blockSignals(True)
        combo.setCurrentIndex(max(0, combo.findData(data["preset"])))
        combo.blockSignals(blocked)
