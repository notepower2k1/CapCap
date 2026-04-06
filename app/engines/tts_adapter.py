from tts_processor import synthesize_text_to_wav_16k_mono


class TTSAdapter:
    def synthesize_segment(
        self,
        *,
        text: str,
        wav_path: str,
        voice: str = "vi_VN-vais1000-medium",
        speed: float = 1.0,
        tmp_dir: str | None = None,
        on_progress: callable = None,
    ) -> str:
        return synthesize_text_to_wav_16k_mono(
            text=text,
            wav_path=wav_path,
            voice=voice,
            speed=speed,
            tmp_dir=tmp_dir,
            on_progress=on_progress,
        )
