"""Exercise ElevenLabs through the real WAV conversion and voice-track pipeline."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "app", ROOT / "ui"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))

from app.engines import elevenlabs_tts
from app.workflows.voice_workflow import VoiceWorkflow
from runtime_paths import subprocess_hidden_kwargs


class TestElevenLabsAudioIntegration(unittest.TestCase):
    def test_model_snapshot_language_and_round_trip_with_real_audio(self):
        ffmpeg = ROOT / "bin" / "ffmpeg" / "ffmpeg.exe"
        if not ffmpeg.is_file():
            self.skipTest("Bundled FFmpeg is required for the native audio pipeline")

        with tempfile.TemporaryDirectory(prefix="capcap-elevenlabs-audio-") as folder:
            work = Path(folder)
            audio_by_model = {}
            for model_id, frequency in (("eleven_flash_v2_5", 440), ("eleven_v3", 880)):
                mp3 = work / f"{model_id}.mp3"
                subprocess.run(
                    [
                        str(ffmpeg), "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=0.4",
                        "-ac", "1", "-ar", "44100", "-c:a", "libmp3lame",
                        "-b:a", "128k", str(mp3),
                    ],
                    check=True, capture_output=True, timeout=20,
                    **subprocess_hidden_kwargs(),
                )
                audio_by_model[model_id] = mp3.read_bytes()

            requests_seen = []

            def synthesize_response(url, **kwargs):
                payload = dict(kwargs["json"])
                self.assertEqual(payload["language_code"], "en")
                requests_seen.append(payload)
                # Settings changing after the first request must not change
                # the model of another subtitle in this same voice job.
                if len(requests_seen) == 1:
                    os.environ["ELEVENLABS_MODEL"] = "eleven_v3"
                return Mock(status_code=200, content=audio_by_model[payload["model_id"]])

            def generate():
                result = VoiceWorkflow(str(work)).run(
                    segments=[
                        {"text": "Welcome to Jos\u00e9's caf\u00e9.", "start": 0.0, "end": 1.0},
                        {"text": "Hello again.", "start": 1.0, "end": 2.0},
                    ],
                    output_dir=str(work / "output"),
                    project_temp_dir=str(work / "tts"),
                    voice_name="elevenlabs:offline-review-voice",
                    timing_sync_mode="Off",
                    target_language="en",
                )
                output = Path(result["voice_track"])
                self.assertTrue(output.is_file())
                self.assertTrue(all(Path(segment["_wav_path"]).is_file() for segment in result["segments"]))
                return output.read_bytes()

            with patch.dict(os.environ, {
                "ELEVENLABS_API_KEY": "offline-test-placeholder",
                "ELEVENLABS_MODEL": "eleven_flash_v2_5",
                "ELEVENLABS_TTS_WORKERS": "1",
                "CAPCAP_RUNTIME_PROFILE": "local",
                "TARGET_LANGUAGE": "vi",
            }), patch(
                "requests.sessions.Session.request",
                side_effect=AssertionError("External HTTP is forbidden in offline audio tests"),
            ), patch.object(elevenlabs_tts.requests, "post", side_effect=synthesize_response):
                first_audio = generate()
                self.assertEqual(len(requests_seen), 2)
                self.assertEqual({request["model_id"] for request in requests_seen}, {"eleven_flash_v2_5"})

                second_audio = generate()
                self.assertEqual(len(requests_seen), 4)
                self.assertEqual({request["model_id"] for request in requests_seen[2:]}, {"eleven_v3"})
                self.assertNotEqual(first_audio, second_audio)

                os.environ["ELEVENLABS_MODEL"] = "eleven_flash_v2_5"
                restored_audio = generate()
                self.assertEqual(len(requests_seen), 4, "The original model's cached audio should remain reusable")
                self.assertEqual(first_audio, restored_audio)


if __name__ == "__main__":
    unittest.main()
