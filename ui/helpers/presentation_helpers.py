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
        "subtitle": "Create Vietnamese subtitles, review them, then export.",
        "voice": "Create a Vietnamese voice track, then export.",
        "both": "Create subtitles and voice, then export.",
    }
    return hints.get(mode, "Choose an output mode to begin.")


def get_export_button_label(mode: str):
    labels = {
        "subtitle": "Export",
        "voice": "Export",
        "both": "Export",
    }
    return labels.get(mode, "Export")


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
        headline = "Choose a source video to begin."
    elif not has_original:
        badge = "Ready to process"
        headline = "Create the original subtitle track next."
    elif mode in ("subtitle", "both") and not has_translated:
        badge = "Subtitle review"
        headline = "Translate the subtitles to Vietnamese."
    elif mode in ("voice", "both") and not has_voice_audio:
        badge = "Voice step"
        headline = "Generate voice or mix for dubbed output."
    else:
        badge = "Ready to preview/export"
        headline = "Preview the result, then export when it looks right."

    if pipeline_active:
        badge = "Processing"
        headline = "CapCap is processing the current pipeline."

    readiness = " • ".join(
        [
            f"Video {'Ready' if video_ready else 'Missing'}",
            f"Original {'Ready' if has_original else 'Pending'}",
            f"Vietnamese {'Ready' if has_applied_subtitles or has_translated else 'Pending'}",
            f"Voice {'Ready' if has_voice_audio else 'Optional'}",
        ]
    )

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
