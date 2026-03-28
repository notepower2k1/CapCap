import os


OUTPUT_MODE_MAPPING = {
    "Vietnamese subtitles only": "subtitle",
    "Vietnamese voice only": "voice",
    "Vietnamese subtitles + voice": "both",
}


def get_output_mode_key(value):
    return OUTPUT_MODE_MAPPING.get(value, "both")


def build_workflow_hint(mode: str, ai_polish_enabled: bool):
    hints = {
        "subtitle": "This mode will create Vietnamese subtitles first. You can review the subtitle text, then export a subtitled video.",
        "voice": "This mode will create a Vietnamese voice track, keep or reuse background audio, then export a video with new audio.",
        "both": "This mode will create both Vietnamese subtitles and Vietnamese voice, then combine them into one final video.",
    }
    polish_hint = " Use 'Rewrite with AI' in the subtitle editor whenever you want to refine the Vietnamese wording."
    return hints.get(mode, "Choose an output mode to begin.") + polish_hint


def get_export_button_label(mode: str):
    labels = {
        "subtitle": "Export Subtitled Video",
        "voice": "Export Vietnamese Voice Video",
        "both": "Export Final Video",
    }
    return labels.get(mode, "Export Final Video")


def build_guidance_state(
    *,
    video_path: str,
    transcript_text: str,
    translated_text: str,
    translated_srt_path: str,
    selected_audio_path: str,
    mode: str,
    pipeline_active: bool,
    mode_label: str,
):
    video_ready = bool(video_path.strip()) and os.path.exists(video_path.strip())
    has_original = bool(transcript_text.strip())
    has_translated = bool(translated_text.strip())
    has_applied_subtitles = bool(translated_srt_path and os.path.exists(translated_srt_path))
    has_voice_audio = bool(selected_audio_path and os.path.exists(selected_audio_path))

    if not video_ready:
        badge = "Waiting for video"
        headline = "Step 1: choose the source video."
    elif not has_original:
        badge = "Ready to process"
        headline = "Next best step: create the original subtitle track."
    elif mode in ("subtitle", "both") and not has_translated:
        badge = "Subtitle review"
        headline = "Original subtitles are ready. Translate them to Vietnamese next."
    elif mode in ("voice", "both") and not has_voice_audio:
        badge = "Voice step"
        headline = "Subtitles are ready. Generate voice/mix when you want dubbed output."
    else:
        badge = "Ready to preview/export"
        headline = "The core assets are ready. Preview the result, then export when it looks right."

    if pipeline_active:
        badge = "Processing"
        headline = "CapCap is running the guided pipeline for you."

    readiness = " | ".join(
        [
            f"Video: {'Ready' if video_ready else 'Missing'}",
            f"Original subtitles: {'Ready' if has_original else 'Pending'}",
            f"Vietnamese subtitles: {'Ready' if has_applied_subtitles or has_translated else 'Pending'}",
            f"Vietnamese voice: {'Ready' if has_voice_audio else 'Optional / Pending'}",
        ]
    ) + f"\nMode: {mode_label}"

    return {
        "badge": badge,
        "headline": headline,
        "readiness": readiness,
        "has_subtitles": has_applied_subtitles or has_translated,
        "has_voice_audio": has_voice_audio,
    }


def build_preview_context_text(*, video_ready: bool, has_subtitles: bool, has_voice_audio: bool, subtitle_source: str, audio_source: str):
    if not video_ready:
        return "Choose a video to start previewing. Subtitle and voice status will appear here as you work."
    return (
        f"Preview is using {subtitle_source}. "
        f"Audio source: {audio_source}. "
        f"Vietnamese subtitles: {'available' if has_subtitles else 'not ready yet'}. "
        f"Vietnamese voice: {'available' if has_voice_audio else 'not ready yet'}."
    )
