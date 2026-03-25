from video_processor import embed_ass_subtitles, embed_subtitles, extract_audio, get_video_dimensions


class FFmpegAdapter:
    def extract_audio(self, video_path: str, audio_output_path: str) -> bool:
        return extract_audio(video_path, audio_output_path)

    def embed_subtitles(self, video_path: str, srt_path: str, output_path: str, *, subtitle_style=None) -> bool:
        subtitle_style = subtitle_style or {}
        return embed_subtitles(
            video_path,
            srt_path,
            output_path,
            alignment=subtitle_style.get("alignment", 2),
            margin_v=subtitle_style.get("margin_v", 30),
            font_name=subtitle_style.get("font_name", "Arial"),
            font_size=subtitle_style.get("font_size", 18),
            font_color=subtitle_style.get("font_color", "&H00FFFFFF"),
            background_box=subtitle_style.get("background_box", False),
        )

    def embed_ass_subtitles(self, video_path: str, ass_path: str, output_path: str) -> bool:
        return embed_ass_subtitles(video_path, ass_path, output_path)

    def get_video_dimensions(self, video_path: str):
        return get_video_dimensions(video_path)
