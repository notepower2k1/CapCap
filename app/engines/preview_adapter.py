from preview_processor import (
    mux_audio_into_video_clip_for_preview,
    mux_audio_into_video_for_preview,
    render_subtitle_frame_preview,
    trim_video_clip,
)


class PreviewAdapter:
    def mux_audio_for_preview(self, video_path: str, audio_path: str, output_video_path: str) -> str:
        return mux_audio_into_video_for_preview(video_path, audio_path, output_video_path)

    def trim_video_clip(self, video_path: str, output_video_path: str, start_seconds: float, duration_seconds: float) -> str:
        return trim_video_clip(video_path, output_video_path, start_seconds, duration_seconds)

    def mux_audio_into_clip(
        self,
        video_path: str,
        audio_path: str,
        output_video_path: str,
        start_seconds: float,
        duration_seconds: float,
    ) -> str:
        return mux_audio_into_video_clip_for_preview(
            video_path,
            audio_path,
            output_video_path,
            start_seconds,
            duration_seconds,
        )

    def render_subtitle_frame(
        self,
        video_path: str,
        srt_path: str,
        output_image_path: str,
        timestamp_seconds: float,
        *,
        subtitle_style=None,
    ) -> str:
        subtitle_style = subtitle_style or {}
        return render_subtitle_frame_preview(
            video_path,
            srt_path,
            output_image_path,
            timestamp_seconds,
            alignment=subtitle_style.get("alignment", 2),
            margin_v=subtitle_style.get("margin_v", 30),
            font_name=subtitle_style.get("font_name", "Arial"),
            font_size=subtitle_style.get("font_size", 18),
            font_color=subtitle_style.get("font_color", "&H00FFFFFF"),
            background_box=subtitle_style.get("background_box", False),
            animation_style=subtitle_style.get("animation", "Static"),
            highlight_color=subtitle_style.get("highlight_color", subtitle_style.get("font_color", "&H00FFFFFF")),
            outline_color=subtitle_style.get("outline_color", "&H00000000"),
            outline_width=subtitle_style.get("outline_width", 2.0),
            shadow_color=subtitle_style.get("shadow_color", "&H80000000"),
            shadow_depth=subtitle_style.get("shadow_depth", 1.0),
            background_color=subtitle_style.get("background_color", "&H80000000"),
            background_alpha=subtitle_style.get("background_alpha", 0.5),
            bold=subtitle_style.get("bold", False),
            preset_key=subtitle_style.get("preset_key", ""),
            manual_highlights=subtitle_style.get("manual_highlights", []),
        )
