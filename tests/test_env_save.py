#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tests/test_env_save.py

Unit tests verifying that updating a .env file that lacks a trailing newline
properly separates existing and newly appended keys without concatenation.
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest
from unittest.mock import MagicMock, patch


def _simulate_main_window_env_save(env_path: str, updates: dict[str, str]) -> None:
    """Simulate the exact .env update logic implemented in ui/main_window.py."""
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            env_lines = f.readlines()

    new_env_lines = []
    handled_keys = set()
    for line in env_lines:
        match = re.match(r"^([^=]+)=.*", line)
        if match:
            k = match.group(1).strip()
            if k == "TRANSCRIPTION_ENGINE":
                continue
            if k in updates:
                new_env_lines.append(f"{k}={updates[k]}\n")
                handled_keys.add(k)
                continue
        clean_line = line if line.endswith("\n") else f"{line}\n"
        new_env_lines.append(clean_line)

    if new_env_lines and not new_env_lines[-1].endswith("\n"):
        new_env_lines[-1] += "\n"

    for k, v in updates.items():
        if k not in handled_keys:
            new_env_lines.append(f"{k}={v}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_env_lines)


class TestEnvSave(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_file = os.path.join(self.temp_dir.name, ".env")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_append_new_key_to_env_without_trailing_newline(self):
        # File ends abruptly without '\n'
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("CAPCAP_GEMINI_REASONING_EFFORT=medium")

        updates = {
            "ELEVENLABS_API_KEY": "sk_test_12345",
            "ELEVENLABS_MODEL": "eleven_multilingual_v2",
        }

        _simulate_main_window_env_save(self.env_file, updates)

        with open(self.env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(
            lines,
            [
                "CAPCAP_GEMINI_REASONING_EFFORT=medium\n",
                "ELEVENLABS_API_KEY=sk_test_12345\n",
                "ELEVENLABS_MODEL=eleven_multilingual_v2\n",
            ],
        )

    def test_update_existing_key_and_append_new_key_without_trailing_newline(self):
        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("FOO=old\nBAR=baz")

        updates = {
            "FOO": "new",
            "QUX": "val",
        }

        _simulate_main_window_env_save(self.env_file, updates)

        with open(self.env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(
            lines,
            [
                "FOO=new\n",
                "BAR=baz\n",
                "QUX=val\n",
            ],
        )

    def test_save_to_empty_or_nonexistent_env(self):
        updates = {"KEY": "value"}
        _simulate_main_window_env_save(self.env_file, updates)

        with open(self.env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(lines, ["KEY=value\n"])

    def test_launcher_save_device_setting_without_trailing_newline(self):
        from ui.views.launcher import LauncherWindow

        with open(self.env_file, "w", encoding="utf-8") as f:
            f.write("OCR_BACKEND=winocr")

        with patch.object(LauncherWindow, "_selected_device", "cuda", create=True):
            with patch("ui.views.launcher.workspace_root", return_value=self.temp_dir.name):
                LauncherWindow._save_device_env()

        with open(self.env_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        self.assertEqual(
            lines,
            [
                "OCR_BACKEND=winocr\n",
                "CAPCAP_DEVICE=cuda\n",
            ],
        )


if __name__ == "__main__":
    unittest.main()
