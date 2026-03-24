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
        )
