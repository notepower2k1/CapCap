from __future__ import annotations

from importlib import import_module
from runtime_profile import is_remote_profile


class EngineRuntime:
    _ADAPTERS = {
        "ffmpeg": ("engines.ffmpeg_adapter", "FFmpegAdapter"),
        "whisper": ("engines.whisper_adapter", "WhisperAdapter"),
        "translator": ("engines.translator_adapter", "TranslatorAdapter"),
        "demucs": ("engines.demucs_adapter", "DemucsAdapter"),
        "preview": ("engines.preview_adapter", "PreviewAdapter"),
        "tts": ("engines.tts_adapter", "TTSAdapter"),
        "audio_mix": ("engines.audio_mix_adapter", "AudioMixAdapter"),
        "subtitle": ("engines.subtitle_adapter", "SubtitleAdapter"),
    }

    def __init__(self):
        self._instances = {}
        self._remote_profile = is_remote_profile()

    def _adapter(self, key: str):
        instance = self._instances.get(key)
        if instance is not None:
            return instance
        module_name, class_name = self._ADAPTERS[key]
        if self._remote_profile and key == "whisper":
            module_name, class_name = ("engines.remote_whisper_adapter", "RemoteWhisperAdapter")
        elif self._remote_profile and key == "translator":
            module_name, class_name = ("engines.remote_translator_adapter", "RemoteTranslatorAdapter")
        elif self._remote_profile and key == "tts":
            module_name, class_name = ("engines.remote_tts_adapter", "RemoteTTSAdapter")
        module = import_module(module_name)
        instance = getattr(module, class_name)()
        self._instances[key] = instance
        return instance

    @property
    def ffmpeg(self):
        return self._adapter("ffmpeg")

    @property
    def whisper(self):
        return self._adapter("whisper")

    @property
    def translator(self):
        return self._adapter("translator")

    @property
    def demucs(self):
        return self._adapter("demucs")

    @property
    def preview(self):
        return self._adapter("preview")

    @property
    def tts(self):
        return self._adapter("tts")

    @property
    def audio_mix(self):
        return self._adapter("audio_mix")

    @property
    def subtitle(self):
        return self._adapter("subtitle")

    def extract_audio(self, video_path: str, audio_output_path: str) -> bool:
        return self.ffmpeg.extract_audio(video_path, audio_output_path)

    def separate_vocals(self, audio_path: str, output_dir: str):
        return self.demucs.separate(audio_path, output_dir)

    def transcribe_audio(self, audio_path: str, model_path: str, *, language: str):
        return self.whisper.transcribe(audio_path, model_path, language=language)

    def translate_srt(
        self,
        srt_text: str,
        *,
        model_path=None,
        src_lang: str = "auto",
        enable_polish: bool = True,
        optimize_subtitles: bool = True,
        style_instruction: str = "",
    ) -> str:
        return self.translator.translate_srt(
            srt_text,
            model_path=model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
            optimize_subtitles=optimize_subtitles,
            style_instruction=style_instruction,
        )

    def translate_segments(
        self,
        segments,
        *,
        model_path=None,
        src_lang: str = "auto",
        enable_polish: bool = True,
        optimize_subtitles: bool = True,
        style_instruction: str = "",
    ):
        return self.translator.translate_segments(
            segments,
            model_path=model_path,
            src_lang=src_lang,
            enable_polish=enable_polish,
            optimize_subtitles=optimize_subtitles,
            style_instruction=style_instruction,
        )

    def rewrite_translation_segments(self, source_segments, translated_segments, *, model_path=None, src_lang: str = "auto", style_instruction: str = ""):
        return self.translator.rewrite_segments(
            source_segments,
            translated_segments,
            model_path=model_path,
            src_lang=src_lang,
            style_instruction=style_instruction,
        )

    def embed_subtitles(self, video_path: str, srt_path: str, output_path: str, *, subtitle_style=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, output_fps=None) -> bool:
        return self.ffmpeg.embed_subtitles(
            video_path,
            srt_path,
            output_path,
            subtitle_style=subtitle_style,
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=output_scale_mode,
            output_fill_focus_x=output_fill_focus_x,
            output_fill_focus_y=output_fill_focus_y,
            output_fps=output_fps,
        )

    def embed_ass_subtitles(self, video_path: str, ass_path: str, output_path: str, *, blur_region=None, target_width=None, target_height=None, output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, output_fps=None) -> bool:
        return self.ffmpeg.embed_ass_subtitles(
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
        return self.ffmpeg.get_video_dimensions(video_path)

    def generate_srt(self, segments, output_path: str) -> str:
        return self.subtitle.generate_srt(segments, output_path)

    def synthesize_segment(
        self,
        *,
        text: str,
        wav_path: str,
        voice: str,
        speed: float = 1.0,
        tmp_dir: str | None = None,
        on_progress: callable = None,
    ) -> str:
        return self.tts.synthesize_segment(text=text, wav_path=wav_path, voice=voice, speed=speed, tmp_dir=tmp_dir, on_progress=on_progress)

    def build_voice_track(self, *, segments, tts_wav_paths, output_wav_path: str, gain_db: float = 0.0) -> str:
        return self.audio_mix.build_voice_track(
            segments=segments,
            tts_wav_paths=tts_wav_paths,
            output_wav_path=output_wav_path,
            gain_db=gain_db,
        )

    def fit_wav_to_duration(
        self,
        *,
        input_wav_path: str,
        output_wav_path: str,
        target_duration_seconds: float,
        mode: str = "off",
    ) -> str:
        return self.audio_mix.fit_wav_to_duration(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            target_duration_seconds=target_duration_seconds,
            mode=mode,
        )

    def change_wav_speed(
        self,
        *,
        input_wav_path: str,
        output_wav_path: str,
        speed_ratio: float,
    ) -> str:
        return self.audio_mix.change_wav_speed(
            input_wav_path=input_wav_path,
            output_wav_path=output_wav_path,
            speed_ratio=speed_ratio,
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
        ducking_amount_db: float = -6.0,
    ) -> str:
        return self.audio_mix.mix_voice_with_background(
            background_wav_path=background_wav_path,
            voice_wav_path=voice_wav_path,
            output_wav_path=output_wav_path,
            background_gain_db=background_gain_db,
            voice_gain_db=voice_gain_db,
            ducking_mode=ducking_mode,
            ducking_segments=ducking_segments,
            ducking_amount_db=ducking_amount_db,
        )

    def mux_audio_for_preview(self, video_path: str, audio_path: str, output_video_path: str, *, target_width=None, target_height=None, output_scale_mode="fit", focus_x=0.5, focus_y=0.5, output_fps=None) -> str:
        return self.preview.mux_audio_for_preview(
            video_path,
            audio_path,
            output_video_path,
            target_width=target_width,
            target_height=target_height,
            output_scale_mode=output_scale_mode,
            focus_x=focus_x,
            focus_y=focus_y,
            output_fps=output_fps,
        )

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
