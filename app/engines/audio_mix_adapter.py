from audio_mixer import build_voice_track_from_srt_segments, change_wav_speed, fit_wav_to_duration, mix_voice_with_background


class AudioMixAdapter:
    def change_wav_speed(
        self,
        *,
        input_wav_path: str,
        output_wav_path: str,
        speed_ratio: float,
    ) -> str:
        return change_wav_speed(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            speed_ratio=speed_ratio,
        )

    def fit_wav_to_duration(
        self,
        *,
        input_wav_path: str,
        output_wav_path: str,
        target_duration_seconds: float,
        mode: str = "off",
    ) -> str:
        return fit_wav_to_duration(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            target_duration_seconds=target_duration_seconds,
            mode=mode,
        )

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
        ducking_mode: str = "off",
        ducking_segments=None,
    ) -> str:
        return mix_voice_with_background(
            background_wav_path=background_wav_path,
            voice_wav_path=voice_wav_path,
            output_wav_path=output_wav_path,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
            ducking_mode=ducking_mode,
            ducking_segments=ducking_segments,
        )
