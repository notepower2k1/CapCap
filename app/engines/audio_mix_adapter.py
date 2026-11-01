from audio_mixer import (
    build_voice_track_from_srt_segments,
    change_wav_speed,
    fit_wav_to_duration,
    mix_voice_with_background,
    mix_original_with_dub,
    mix_audio_tracks,
    trim_trailing_silence,
    trim_tts_boundary_silence,
)


class AudioMixAdapter:
    def trim_tts_boundary_silence(self, *, input_wav_path: str, output_wav_path: str) -> str:
        return trim_tts_boundary_silence(input_wav_path=input_wav_path, output_wav_path=output_wav_path)

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
        smart_min_ratio: float = 0.77,
        smart_max_ratio: float = 1.15,
    ) -> str:
        return fit_wav_to_duration(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            target_duration_seconds=target_duration_seconds,
            mode=mode,
            smart_min_ratio=smart_min_ratio,
            smart_max_ratio=smart_max_ratio,
        )

    def trim_trailing_silence(
        self,
        *,
        input_wav_path: str,
        output_wav_path: str,
        silence_threshold: float = -40.0,
        min_silence_duration: float = 0.5,
    ) -> str:
        return trim_trailing_silence(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            silence_threshold=silence_threshold,
            min_silence_duration=min_silence_duration,
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
        ducking_amount_db: float = 0.0,
    ) -> str:
        return mix_voice_with_background(
            background_wav_path=background_wav_path,
            voice_wav_path=voice_wav_path,
            output_wav_path=output_wav_path,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
            ducking_mode=ducking_mode,
            ducking_segments=ducking_segments,
            ducking_amount_db=ducking_amount_db,
        )

    def mix_original_with_dub(
        self,
        *,
        original_wav_path: str,
        dub_wav_path: str,
        output_wav_path: str,
        original_gain_db: float = 0.0,
        dub_gain_db: float = 0.0,
    ) -> str:
        return mix_original_with_dub(
            original_wav_path=original_wav_path,
            dub_wav_path=dub_wav_path,
            output_wav_path=output_wav_path,
            original_gain_db=original_gain_db,
            dub_gain_db=dub_gain_db,
        )

    def mix_audio_tracks(
        self,
        *,
        tracks,
        output_wav_path: str,
        total_duration_ms: int | None = None,
        sample_rate: int = 16000,
    ) -> str:
        return mix_audio_tracks(
            tracks=tracks,
            output_wav_path=output_wav_path,
            total_duration_ms=total_duration_ms,
            sample_rate=sample_rate,
        )
