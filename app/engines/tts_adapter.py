from tts_processor import edge_tts_to_wav_16k_mono


class TTSAdapter:
    def synthesize_segment(
        self,
        *,
        text: str,
        wav_path: str,
        voice: str = "vi-VN-HoaiMyNeural",
        tmp_dir: str | None = None,
    ) -> str:
        return edge_tts_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice=voice,
            tmp_dir=tmp_dir,
        )
