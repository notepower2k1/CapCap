"""Resolve installed/bundled font families without distributing font files."""
from functools import lru_cache
import os
from pathlib import Path


@lru_cache(maxsize=1)
def _faces():
    from PIL import ImageFont
    from runtime_paths import asset_path
    directories = [Path(asset_path("fonts")), Path(os.environ.get("WINDIR", "C:/Windows"))/"Fonts",
                   Path(os.environ.get("LOCALAPPDATA", ""))/"Microsoft/Windows/Fonts"]
    result = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in (".ttf", ".otf", ".ttc"):
                continue
            try:
                family, face = ImageFont.truetype(str(path), 12).getname()
                result.append((family.casefold(), face.casefold(), str(path.resolve())))
            except (OSError, ValueError):
                continue
    return result


def font_path(family, bold=False, italic=False):
    matches = [(int(("bold" in face) != bold) + int(("italic" in face or "oblique" in face) != italic), path)
               for name, face, path in _faces() if name == str(family).casefold()]
    return min(matches)[1] if matches else ""


def apply_font(material, style, family):
    path = font_path(family, bool(style.get("bold")), bool(style.get("italic")))
    material.update(font_name=family, font_path=path, font_id="")
    style["font"] = {"id": "", "path": path}
    return bool(path)
