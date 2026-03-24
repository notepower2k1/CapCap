from __future__ import annotations

from engines import (
    AudioMixAdapter,
    DemucsAdapter,
    FFmpegAdapter,
    PreviewAdapter,
    SubtitleAdapter,
    TranslatorAdapter,
    TTSAdapter,
    WhisperAdapter,
)


class EngineRuntime:
    def __init__(self):
        self.ffmpeg = FFmpegAdapter()
        self.whisper = WhisperAdapter()
        self.translator = TranslatorAdapter()
        self.demucs = DemucsAdapter()
        self.preview = PreviewAdapter()
        self.tts = TTSAdapter()
        self.audio_mix = AudioMixAdapter()
        self.subtitle = SubtitleAdapter()

    def extract_audio(self, video_path: str, audio_output_path: str) -> bool:
        return self.ffmpeg.extract_audio(video_path, audio_output_path)

    def separate_vocals(self, audio_path: str, output_dir: str):
        return self.demucs.separate(audio_path, output_dir)

    def transcribe_audio(self, audio_path: str, model_path: str, *, language: str):
        return self.whisper.transcribe(audio_path, model_path, language=language)

    def translate_srt(self, srt_text: str, *, model_path=None, src_lang: str = "auto", enable_polish: bool = True) -> str:
        return self.translator.translate_srt(
            srt_text,
            model_path=model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
        )

    def translate_segments(self, segments, *, model_path=None, src_lang: str = "auto", enable_polish: bool = True):
        return self.translator.translate_segments(
            segments,
            model_path=model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
        )

    def embed_subtitles(self, video_path: str, srt_path: str, output_path: str, *, subtitle_style=None) -> bool:
        return self.ffmpeg.embed_subtitles(
            video_path,
            srt_path,
            output_path,
            subtitle_style=subtitle_style,
        )

    def get_video_dimensions(self, video_path: str):
        return self.ffmpeg.get_video_dimensions(video_path)

    def generate_srt(self, segments, output_path: str) -> str:
        return self.subtitle.generate_srt(segments, output_path)

    def synthesize_segment(self, *, text: str, wav_path: str, voice: str, tmp_dir: str | None = None) -> str:
        return self.tts.synthesize_segment(text=text, wav_path=wav_path, voice=voice, tmp_dir=tmp_dir)

    def build_voice_track(self, *, segments, tts_wav_paths, output_wav_path: str, gain_db: float = 0.0) -> str:
        return self.audio_mix.build_voice_track(
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
        return self.audio_mix.mix_voice_with_background(
            background_wav_path=background_wav_path,
            voice_wav_path=voice_wav_path,
            output_wav_path=output_wav_path,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
        )

    def mux_audio_for_preview(self, video_path: str, audio_path: str, output_video_path: str) -> str:
        return self.preview.mux_audio_for_preview(video_path, audio_path, output_video_path)

    def trim_video_clip(self, video_path: str, output_video_path: str, start_seconds: float, duration_seconds: float) -> str:
        return self.preview.trim_video_clip(video_path, output_video_path, start_seconds, duration_seconds)

    def mux_audio_into_clip(
        self,
        video_path: str,
        audio_path: str,
        output_video_path: str,
        start_seconds: float,
        duration_seconds: float,
    ) -> str:
        return self.preview.mux_audio_into_clip(
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
        return self.preview.render_subtitle_frame(
            video_path,
            srt_path,
            output_image_path,
            timestamp_seconds,
            subtitle_style=subtitle_style,
        )
