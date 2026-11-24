#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_capcut_tts.py

Unit tests for CapCut TTS catalog filtering, error detail formatting,
and automatic Edge TTS fallback for unsupported neural voice names.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root and app are in sys.path
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
APP_DIR = os.path.join(PROJECT_ROOT, "app")
for p in (PROJECT_ROOT, APP_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

from app.capcut.tts import (
    CapCutTTSClient,
    list_capcut_voices,
    synthesize_capcut_tts_wav_16k_mono,
)


class TestCapCutTTS(unittest.TestCase):
    """Test CapCut voice catalog filtering and synthesis routing."""

    def test_list_capcut_voices_filters_out_neural_voices(self):
        """Verify list_capcut_voices excludes Microsoft Neural voices."""
        voices = list_capcut_voices()
        self.assertGreater(len(voices), 0)
        for v in voices:
            provider_voice = v.get("provider_voice", "").lower()
            self.assertFalse(
                "neural" in provider_voice,
                f"Found neural voice in CapCut catalog: {v['id']}",
            )

    @patch("app.tts_processor.edge_tts_to_wav")
    def test_neural_voice_fallback_to_edge_tts(self, mock_edge_tts):
        """Verify requesting a *Neural voice under CapCut routes directly to Edge TTS."""
        mock_edge_tts.return_value = "output_edge.wav"

        result = synthesize_capcut_tts_wav_16k_mono(
            text="Hello world",
            wav_path="output_edge.wav",
            voice_id="capcut:vi-VN-HoaiMyNeural",
            speed=1.0,
        )

        self.assertEqual(result, "output_edge.wav")
        mock_edge_tts.assert_called_once()
        call_kwargs = mock_edge_tts.call_args[1]
        self.assertEqual(call_kwargs["voice"], "vi-VN-HoaiMyNeural")
        self.assertEqual(call_kwargs["rate"], "+0%")

    @patch("app.tts_processor.edge_tts_to_wav")
    @patch.object(CapCutTTSClient, "synthesize")
    def test_invalid_speaker_fallback_to_edge_tts(self, mock_synthesize, mock_edge_tts):
        """Verify TTSInvalidSpeaker error triggers Edge TTS fallback for neural voices."""
        mock_synthesize.side_effect = RuntimeError(
            "CapCut TTS task failed: TTSInvalidSpeaker (code 40402004)"
        )
        mock_edge_tts.return_value = "fallback.wav"

        result = synthesize_capcut_tts_wav_16k_mono(
            text="Hello",
            wav_path="fallback.wav",
            voice_id="vi-VN-NamMinhNeural",
            speed=1.25,
        )

        self.assertEqual(result, "fallback.wav")
        mock_edge_tts.assert_called_once()
        call_kwargs = mock_edge_tts.call_args[1]
        self.assertEqual(call_kwargs["rate"], "+25%")


if __name__ == "__main__":
    unittest.main()
