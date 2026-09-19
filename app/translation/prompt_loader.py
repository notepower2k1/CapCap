from __future__ import annotations

import json
import re
from pathlib import Path


_PLACEHOLDER_RE = re.compile(r"\{\{([a-zA-Z][a-zA-Z0-9_]*)\}\}")


class PromptTemplateError(RuntimeError):
    """Raised when a translation prompt template cannot be loaded or rendered."""


def prompt_directory() -> Path:
    candidates: list[Path] = []
    try:
        from runtime_paths import app_path, bundle_root, workspace_root

        candidates.extend(
            [
                Path(app_path("translation", "prompts")),
                Path(bundle_root()) / "app" / "translation" / "prompts",
                Path(bundle_root()) / "translation" / "prompts",
                Path(workspace_root()) / "app" / "translation" / "prompts",
            ]
        )
    except Exception:
        try:
            from app.runtime_paths import app_path, bundle_root, workspace_root

            candidates.extend(
                [
                    Path(app_path("translation", "prompts")),
                    Path(bundle_root()) / "app" / "translation" / "prompts",
                    Path(bundle_root()) / "translation" / "prompts",
                    Path(workspace_root()) / "app" / "translation" / "prompts",
                ]
            )
        except Exception:
            pass

    this_dir = Path(__file__).resolve().parent
    candidates.extend(
        [
            this_dir / "prompts",
            this_dir.parent / "translation" / "prompts",
            this_dir.parent / "app" / "translation" / "prompts",
        ]
    )

    for c in candidates:
        if c.is_dir():
            return c
    return this_dir / "prompts"


def load_prompt(template_name: str) -> str:
    safe_name = str(template_name or "").strip()
    if not safe_name or Path(safe_name).name != safe_name:
        raise PromptTemplateError(f"Invalid translation prompt name: {template_name!r}")

    path = prompt_directory() / safe_name
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise PromptTemplateError(f"Could not read translation prompt: {path}") from exc


def load_prompt_options(template_name: str) -> list[tuple[str, str]]:
    try:
        catalog = json.loads(load_prompt(template_name))
    except json.JSONDecodeError as exc:
        raise PromptTemplateError(
            f"Translation prompt catalog {template_name!r} contains invalid JSON"
        ) from exc
    if not isinstance(catalog, list):
        raise PromptTemplateError(f"Translation prompt catalog {template_name!r} must be a JSON array")
    options = []
    for item in catalog:
        if not isinstance(item, dict):
            raise PromptTemplateError(f"Translation prompt catalog {template_name!r} contains an invalid item")
        label = str(item.get("label", "") or "").strip()
        instruction = str(item.get("instruction", "") or "").strip()
        if not label or not instruction:
            raise PromptTemplateError(f"Translation prompt catalog {template_name!r} requires label and instruction")
        options.append((label, instruction))
    return options


def render_prompt(template_name: str, **values) -> str:
    template = load_prompt(template_name)
    required = set(_PLACEHOLDER_RE.findall(template))
    defaults = {"context_guidance": "", "style_clause": ""}
    for k, v in defaults.items():
        if k not in values:
            values[k] = v
    missing = sorted(key for key in required if key not in values)
    if missing:
        raise PromptTemplateError(
            f"Translation prompt {template_name!r} is missing values for: {', '.join(missing)}"
        )

    return _PLACEHOLDER_RE.sub(lambda match: str(values[match.group(1)]), template).strip()


def load_translation_presets() -> list[dict]:
    """Load the registered translation prompt presets from JSON catalog."""
    path = prompt_directory() / "translation_prompt_presets.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def get_preset_by_id(preset_id: str) -> dict | None:
    presets = load_translation_presets()
    for item in presets:
        if item.get("id") == preset_id:
            return item
    return None


def render_preset_prompt(preset_id: str, **values) -> str:
    """Render a preset prompt template with source/target languages and style."""
    preset = get_preset_by_id(preset_id)
    preset_file = f"presets/{preset.get('file', 'general_default.md')}" if preset else "presets/general_default.md"
    full_path = prompt_directory() / preset_file
    if not full_path.exists():
        full_path = prompt_directory() / "subtitle_translation.system.md"

    if full_path.exists():
        template = full_path.read_text(encoding="utf-8").strip()
    else:
        template = (
            "You are an expert subtitle translator. Translate the given subtitles accurately, "
            "naturally, and concisely into {{target_lang}} while preserving original meaning, tone, "
            "and subtitle line numbers. {{style_clause}}\n{{context_guidance}}"
        )
    merged_values = {
        "source_lang": "auto",
        "target_lang": "vi",
        "style_clause": "",
        "context_guidance": "",
    }
    merged_values.update(values)
    rendered = _PLACEHOLDER_RE.sub(lambda match: str(merged_values.get(match.group(1), "")), template).strip()
    guidance = str(merged_values.get("context_guidance", "")).strip()
    if guidance and "{{context_guidance}}" not in template:
        closing = "Never merge, omit, reorder, or split cue numbers."
        if closing in rendered:
            rendered = rendered.replace(closing, f"{guidance}\n\n{closing}")
        else:
            rendered = f"{rendered}\n\n{guidance}"
    return rendered


def extract_preset_rules(preset_id: str) -> str:
    """Extract the specific naming, honorific, and address guidelines from a preset."""
    if not preset_id or preset_id == "general_default":
        return ""
    preset = get_preset_by_id(preset_id)
    if not preset:
        return ""
    preset_file = f"presets/{preset.get('file', '')}"
    full_path = prompt_directory() / preset_file
    if not full_path.exists():
        return ""
    try:
        content = full_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    lines = content.splitlines()
    rule_lines = []
    in_rules = False
    for line in lines:
        if line.startswith("### "):
            in_rules = True
        if in_rules:
            if line.strip().startswith("Never merge, omit"):
                break
            rule_lines.append(line)
    return "\n".join(rule_lines).strip()

