import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "ui"), str(ROOT / "app"), str(ROOT)]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui import main_window
from ui.main_window import VideoTranslatorGUI


class _SyncThread:
    def __init__(self, target=None, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


class VoicePreloadFailureTests(unittest.TestCase):
    def _gui(self, impl):
        logs = []
        gui = SimpleNamespace(
            get_active_voice_name=lambda: "vi-voice",
            free_voice_combo=SimpleNamespace(currentData=lambda role: "entry"),
            VOICE_ENTRY_ID_ROLE=0,
            voice_catalog_map={"entry": {"provider": "piper"}},
            _preload_tts_voice_impl=impl,
            log=logs.append,
        )
        return gui, logs

    def _run(self, gui):
        # Run the worker thread synchronously, but queue QTimer.singleShot callbacks and run them after
        # the worker returns, as the Qt event loop does (the worker's except block has exited by then).
        queued = []
        with patch.object(main_window.threading, "Thread", _SyncThread), \
                patch.object(main_window.QTimer, "singleShot", lambda ms, fn: queued.append(fn)):
            VideoTranslatorGUI._preload_active_voice_if_needed(gui)
        for callback in queued:
            callback()

    def test_failed_preload_clears_inflight_and_logs_the_reason(self):
        def impl(voice):
            raise RuntimeError("piper voice missing")

        gui, logs = self._gui(impl)
        self._run(gui)

        self.assertEqual(gui._voice_preload_inflight, "")
        self.assertEqual(len(logs), 1)
        self.assertIn("piper voice missing", logs[0])

    def test_successful_preload_marks_voice_ready(self):
        gui, logs = self._gui(lambda voice: None)
        self._run(gui)

        self.assertEqual(gui._voice_preload_inflight, "")
        self.assertEqual(gui._voice_preloaded_name, "vi-voice")


if __name__ == "__main__":
    unittest.main()
