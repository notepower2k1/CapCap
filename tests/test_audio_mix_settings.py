import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, MethodType
import unittest
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
for directory in (ROOT, ROOT / "app", ROOT / "ui"):
    sys.path.insert(0, str(directory))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QSlider, QLabel, QComboBox
from ui.main_window import VideoTranslatorGUI
from ui.utils.audio_mix_settings import KEY, PRESETS, CONTROLS, read_mix, restore_mix


class AudioMixSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "settings.ini")

    def host(self):
        host = SimpleNamespace(settings=QSettings(self.path, QSettings.IniFormat),
            _sync_audio_track_volume=MagicMock(), _apply_audio_mix_to_tracks=MagicMock(),
            _refresh_music_layer_summary=MagicMock(), _music_audio_tracks=lambda: [],
            _schedule_preview_audio_refresh=MagicMock(), AUDIO_MIX_PRESETS=PRESETS)
        host.audio_mix_preset_combo = QComboBox()
        for preset in (*PRESETS, "custom"):
            host.audio_mix_preset_combo.addItem(preset, preset)
        for prefix in CONTROLS.values():
            slider = QSlider()
            slider.setRange(0, 200)
            setattr(host, prefix + "_slider", slider)
            setattr(host, prefix + "_label", QLabel())
        restore_mix(host)
        host._set_audio_mix_preset_custom = MethodType(VideoTranslatorGUI._set_audio_mix_preset_custom, host)
        host.audio_mix_preset_combo.currentIndexChanged.connect(lambda *_: VideoTranslatorGUI.on_audio_mix_preset_changed(host))
        for prefix, method in (("audio_a1_volume", "on_audio_a1_volume_changed"),
                               ("audio_a2_volume", "on_audio_a2_volume_changed"),
                               ("audio_music_volume", "on_audio_music_volume_changed")):
            getattr(host, prefix + "_slider").valueChanged.connect(MethodType(getattr(VideoTranslatorGUI, method), host))
        return host

    def test_manual_mix_and_preset_survive_reopening_settings(self):
        first = self.host()
        first.audio_a1_volume_slider.setValue(17)
        first.audio_a2_volume_slider.setValue(135)
        first.audio_music_volume_slider.setValue(0)
        first.settings.sync()
        reopened = self.host()
        self.assertEqual(read_mix(reopened.settings), {"original": 17, "tts": 135, "music": 0, "preset": "custom"})
        self.assertEqual(reopened.audio_a2_volume_slider.value(), 135)
        reopened._sync_audio_track_volume.assert_not_called()
        first.audio_mix_preset_combo.setCurrentIndex(first.audio_mix_preset_combo.findData("balanced"))
        first.settings.sync()
        again = self.host()
        self.assertEqual(again.audio_mix_preset_combo.currentData(), "balanced")
        self.assertEqual(again.audio_a1_volume_slider.value(), 100)
        self.assertEqual(again.audio_a2_volume_slider.value(), 100)

    def test_named_mix_applies_all_three_volumes_and_remembers_them(self):
        from ui.utils.audio_mix_settings import apply_preset, capture_preset
        host = self.host()
        for method in ("on_audio_a1_volume_changed", "on_audio_a2_volume_changed", "on_audio_music_volume_changed"):
            setattr(host, method, MethodType(getattr(VideoTranslatorGUI, method), host))
        apply_preset(host, dict(original=23, tts=140, music=12))
        self.assertEqual(capture_preset(host), dict(original=23, tts=140, music=12))
        host._sync_audio_track_volume.assert_any_call("A1 Audio", 23)
        host._sync_audio_track_volume.assert_any_call("TS1", 140)
        host._sync_audio_track_volume.assert_any_call("A2 Music", 12)
        self.assertEqual(read_mix(host.settings), dict(original=23, tts=140, music=12, preset="custom"))

    def test_project_mix_takes_priority_without_replacing_last_user_defaults(self):
        host = self.host()
        host.audio_a1_volume_slider.setValue(25)
        cached = host.settings.value(KEY)
        tracks = [SimpleNamespace(name="A1 Audio", metadata={"_volume": 80}),
                  SimpleNamespace(name="A2 Dub", metadata={"_volume": 20}),
                  SimpleNamespace(name="A2 Music", metadata={"_volume": 42})]
        host.timeline = SimpleNamespace(_timeline=SimpleNamespace(tracks=tracks))
        VideoTranslatorGUI._sync_audio_mix_controls_from_tracks(host)
        self.assertEqual(host.audio_a1_volume_slider.value(), 80)
        self.assertEqual(host.audio_a2_volume_slider.value(), 20)
        self.assertEqual(host.audio_mix_preset_combo.currentData(), "prefer_original")
        self.assertEqual(host.settings.value(KEY), cached)

    def test_new_track_uses_default_and_existing_project_volume_is_preserved(self):
        from app.layers.timeline import Timeline
        from app.layers.sync_bridge import ensure_v1_a1_tracks
        timeline = Timeline(duration=5)
        ensure_v1_a1_tracks(timeline, "video.mp4", 5, original_volume=23)
        track = next(t for t in timeline.tracks if t.name == "A1 Audio")
        self.assertEqual(track.metadata["_volume"], 23)
        track.metadata["_volume"] = 77
        ensure_v1_a1_tracks(timeline, "video.mp4", 5, original_volume=23)
        self.assertEqual(track.metadata["_volume"], 77)

    def test_bad_cache_is_bounded_and_does_not_break_startup(self):
        host = self.host()
        host.settings.setValue(KEY, "invalid JSON")
        self.assertEqual(read_mix(host.settings)["original"], 50)
        host.settings.setValue(KEY, '{"original":-30,"tts":999,"music":"nan","preset":"balanced"}')
        self.assertEqual(read_mix(host.settings), {"original": 0, "tts": 200, "music": 30, "preset": "custom"})
