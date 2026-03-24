from subtitle_builder import generate_srt


class SubtitleAdapter:
    def generate_srt(self, segments, output_path: str) -> str:
        generate_srt(segments, output_path)
        return output_path
