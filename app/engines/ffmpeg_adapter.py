from video_processor import embed_ass_subtitles, embed_subtitles, extract_audio, get_video_dimensions


class FFmpegAdapter:
    def extract_audio(self, video_path: str, audio_output_path: str) -> bool:
        return extract_audio(video_path, audio_output_path)

    def embed_subtitles(self, video_path: str, srt_path: str, output_path: str, *, subtitle_style=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, output_fps=None) -> bool:
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
            auto_keyword_highlight=subtitle_style.get("auto_keyword_highlight", False),
            animation_duration=subtitle_style.get("animation_duration", 0.22),
            manual_highlights=subtitle_style.get("manual_highlights", []),
            word_timings=subtitle_style.get("word_timings", []),
            karaoke_timing_mode=subtitle_style.get("karaoke_timing_mode", "vietnamese"),
            custom_position_enabled=subtitle_style.get("custom_position_enabled", False),
            custom_position_x=subtitle_style.get("custom_position_x", 50),
            custom_position_y=subtitle_style.get("custom_position_y", 86),
            single_line=subtitle_style.get("single_line", False),
            blur_region=subtitle_style.get("blur_region"),
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=output_scale_mode,
            output_fill_focus_x=output_fill_focus_x,
            output_fill_focus_y=output_fill_focus_y,
            output_fps=output_fps,
        )

    def embed_ass_subtitles(self, video_path: str, ass_path: str, output_path: str, *, blur_region=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, output_fps=None) -> bool:
        return embed_ass_subtitles(
            video_path,
            ass_path,
            output_path,
            blur_region=blur_region,
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=output_scale_mode,
            output_fill_focus_x=output_fill_focus_x,
            output_fill_focus_y=output_fill_focus_y,
            output_fps=output_fps,
        )

    def get_video_dimensions(self, video_path: str):
        return get_video_dimensions(video_path)

