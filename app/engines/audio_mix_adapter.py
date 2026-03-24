from audio_mixer import build_voice_track_from_srt_segments, mix_voice_with_background


class AudioMixAdapter:
    def build_voice_track(self, *, segments, tts_wav_paths, output_wav_path: str, gain_db: float = 0.0) -> str:
        return build_voice_track_from_srt_segments(
            segments=segments,
            tts_wav_paths=tts_wav_paths,
            output_wav_path=output_wav_path,
            gain_db=gain_db,
        )

    def mix_voice_with_background(
        self,
        *,
        background_wav_path: str,
        voice_wav_path: str,
        output_wav_path: str,
        background_gain_db: float = 0.0,
        voice_gain_db: float = 0.0,
    ) -> str:
        return mix_voice_with_background(
            background_wav_path=background_wav_path,
            voice_wav_path=voice_wav_path,
            output_wav_path=output_wav_path,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
        )
