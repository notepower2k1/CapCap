import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils.browser_extension_utils import collect_browser_extension_data, get_browser_extension_dir


class TestBrowserExtensionBundle(unittest.TestCase):
    def test_runtime_files_and_manifest_references_are_bundled(self):
        import json
        root = Path(__file__).resolve().parents[1]
        data = collect_browser_extension_data(root)
        bundled = {Path(source).relative_to(root / "browser_extension").as_posix() for source, _ in data}
        manifest = json.loads((root / "browser_extension/manifest.json").read_text(encoding="utf-8"))
        self.assertIn("manifest.json", bundled)
        self.assertIn(manifest["background"]["service_worker"], bundled)
        self.assertIn(manifest["action"]["default_popup"], bundled)
        for script in manifest["content_scripts"]:
            for name in script["js"]:
                self.assertIn(name, bundled)
        for name in manifest["icons"].values():
            self.assertIn(name, bundled)
        self.assertTrue(all("node_modules" not in source and "tests" not in source for source, _ in data))

    def test_frozen_install_resolves_internal_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "_internal/browser_extension"
            extension.mkdir(parents=True)
            (extension / "manifest.json").write_text("{}")
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(root / "_internal"), create=True):
                self.assertEqual(get_browser_extension_dir(), str(extension))

    def test_missing_extension_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", directory, create=True), patch.object(sys, "executable", str(Path(directory) / "CapCap.exe")):
                with self.assertRaises(FileNotFoundError):
                    get_browser_extension_dir()

    def test_source_checkout_resolves_extension(self):
        self.assertTrue((Path(get_browser_extension_dir()) / "manifest.json").is_file())
