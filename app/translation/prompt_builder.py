import os
from .prompt_loader import extract_preset_rules, render_preset_prompt, render_prompt


def build_translation_messages(
    *,
    source_texts: list[str],
    translated_texts: list[str] | None,
    source_speakers: list[str] | None = None,
    src_lang: str,
    target_lang: str,
    style_instruction: str = "",
    custom_system_prompt: str = "",
    context_guidance: str = "",
) -> tuple[str, str]:
    """Build the system/user message pair from editable Markdown templates or custom prompt."""
    style_value = str(style_instruction or "").strip()
    style_clause = f" Style: {style_value}" if style_value else ""
    lowered_style = style_value.lower()
    dubbing_mode = "[mode=dubbing_rewrite]" in lowered_style
    ocr_capture_mode = "[mode=ocr_capture]" in lowered_style
    is_direct = not translated_texts

    has_speakers = bool(source_speakers and any(str(s).strip() for s in source_speakers))

    if is_direct and ocr_capture_mode:
        prompt_key = "ocr_translation"
        lines = [
            f"{index + 1}. <OCR_TEXT>{' '.join(str(text or '').splitlines())}</OCR_TEXT>"
            for index, text in enumerate(source_texts)
        ]
    elif is_direct:
        prompt_key = "subtitle_translation"
        if has_speakers:
            lines = [
                f"{index + 1}. [{str(spk).strip()}]: {text}" if str(spk).strip() else f"{index + 1}. {text}"
                for index, (text, spk) in enumerate(zip(source_texts, source_speakers or []))
            ]
        else:
            lines = [f"{index + 1}. {text}" for index, text in enumerate(source_texts)]
    else:
        prompt_key = "dubbing_rewrite" if dubbing_mode else "subtitle_refinement"
        lines = [
            f"{index + 1}. {source} ||| {draft}"
            for index, (source, draft) in enumerate(zip(source_texts, translated_texts or []))
        ]

    values = {
        "source_lang": str(src_lang or "auto"),
        "target_lang": str(target_lang or "vi"),
        "style_clause": style_clause,
        "context_guidance": str(context_guidance or "").strip(),
    }
    if custom_system_prompt and str(custom_system_prompt).strip() and is_direct:
        system_message = str(custom_system_prompt).strip()
        for k, v in values.items():
            system_message = system_message.replace(f"{{{{{k}}}}}", str(v))
    else:
        preset_id = (os.getenv("CAPCAP_TRANSLATION_PRESET_ID") or "").strip()
        if preset_id and is_direct and not ocr_capture_mode:
            try:
                system_message = render_preset_prompt(preset_id, **values)
            except Exception:
                system_message = render_prompt(f"{prompt_key}.system.md", **values)
        else:
            system_message = render_prompt(f"{prompt_key}.system.md", **values)
            # Inherit active preset's naming and address rules during rewrite/refinement
            if not is_direct and preset_id and str(target_lang).lower().split("-")[0] == "vi":
                try:
                    rules = extract_preset_rules(preset_id)
                    if rules:
                        system_message += (
                            f"\n\n### Inherited Genre & Pronoun Rules ({preset_id}):\n"
                            "While refining and adjusting the style, you MUST strictly preserve "
                            "these character names, honorifics, and address forms (xưng hô):\n"
                            f"{rules}\n"
                        )
                except Exception:
                    pass

    if context_guidance and str(context_guidance).strip():
        guidance_text = str(context_guidance).strip()
        if "{{context_guidance}}" in system_message:
            system_message = system_message.replace("{{context_guidance}}", guidance_text)
        elif guidance_text not in system_message:
            system_message += f"\n\n{guidance_text}"

    if has_speakers:
        speaker_instruction = (
            "\n\n### Speaker Tags Instruction:\n"
            "Input cues contain speaker tags like [SPEAKER_00] or [SPEAKER_01]. "
            "Use them strictly to track who is speaking and maintain consistent address forms (xưng hô). "
            "In your output, you MUST return ONLY 'N. translated text' WITHOUT the speaker tags."
        )
        if "Speaker Tags Instruction" not in system_message:
            system_message += speaker_instruction

    user_message = "\n".join(lines)
    return system_message, user_message


