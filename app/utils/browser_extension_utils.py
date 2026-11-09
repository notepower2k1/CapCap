"""Files needed by the unpacked extension, in source and frozen builds."""
from pathlib import Path
import sys


def collect_browser_extension_data(project_root):
    root = Path(project_root) / "browser_extension"
    files = [root / name for name in (
        "manifest.json", "background.js", "content.js", "popup.js", "popup.html",
    )]
    files.extend(sorted((root / "icons").glob("*.png")))
    for file in files:
        if not file.is_file():
            raise FileNotFoundError(f"Browser extension file missing: {file}")
    return [(str(file), str(Path("browser_extension") / file.relative_to(root).parent)) for file in files]


def get_browser_extension_dir():
    if getattr(sys, "frozen", False):
        roots = [Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)), Path(sys.executable).parent]
    else:
        roots = [Path(__file__).resolve().parents[2]]
    for root in roots:
        directory = root / "browser_extension"
        if (directory / "manifest.json").is_file():
            return str(directory)
    raise FileNotFoundError("Bundled browser extension was not found. Please rebuild or reinstall CapCap.")
