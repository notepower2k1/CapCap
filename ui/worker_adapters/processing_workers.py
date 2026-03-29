import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QThread, Signal

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app")
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from services import EngineRuntime, WorkflowRuntime


class VocalSeparationWorker(QThread):
    finished = Signal(str, str, str)

    def __init__(self, audio_path, output_dir):
        super().__init__()
        self.audio_path = audio_path
        self.output_dir = output_dir

    def run(self):
        try:
            engine = EngineRuntime()
            vocal_path, music_path = engine.separate_vocals(self.audio_path, self.output_dir)
            if vocal_path and music_path:
                self.finished.emit(vocal_path, music_path, "")
            else:
                self.finished.emit("", "", "Failed to separate audio stems.")
        except ImportError as exc:
            self.finished.emit("", "", str(exc))
        except Exception as exc:
            self.finished.emit("", "", f"Unexpected error: {str(exc)}")


class ExtractionWorker(QThread):
    finished = Signal(bool, str)

    def __init__(self, video_path, audio_output_path):
        super().__init__()
        self.video_path = video_path
        self.audio_output_path = audio_output_path

    def run(self):
        try:
            engine = EngineRuntime()
            success = engine.extract_audio(self.video_path, self.audio_output_path)
            self.finished.emit(success, self.audio_output_path)
        except Exception as exc:
            self.finished.emit(False, str(exc))


class TranscriptionWorker(QThread):
    finished = Signal(list)

    def __init__(self, audio_path, model_path, language):
        super().__init__()
        self.audio_path = audio_path
        self.model_path = model_path
        self.language = language

    def run(self):
        try:
            engine = EngineRuntime()
            segments = engine.transcribe_audio(self.audio_path, self.model_path, language=self.language)
            self.finished.emit(segments if segments else [])
        except Exception as exc:
            print(f"Transcription Thread Error: {exc}")
            self.finished.emit([])


class TranslationWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, srt_text, model_path, src_lang, enable_polish):
        super().__init__()
        self.srt_text = srt_text
        self.model_path = model_path
        self.src_lang = src_lang
        self.enable_polish = enable_polish

    def run(self):
        try:
            engine = EngineRuntime()
            translated_srt = engine.translate_srt(
                self.srt_text,
                model_path=self.model_path,
                src_lang=self.src_lang,
                enable_polish=self.enable_polish,
            )
            self.finished.emit(translated_srt, "")
        except Exception as exc:
            print(f"Translation Thread Error: {exc}")
            self.finished.emit("", str(exc))


class RewriteTranslationWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, source_segments, translated_segments, src_lang, style_instruction=""):
        super().__init__()
        self.source_segments = source_segments
        self.translated_segments = translated_segments
        self.src_lang = src_lang
        self.style_instruction = style_instruction

    def run(self):
        try:
            engine = EngineRuntime()
            rewritten_segments = engine.rewrite_translation_segments(
                self.source_segments,
                self.translated_segments,
                src_lang=self.src_lang,
                style_instruction=self.style_instruction,
            )
            from translation.srt_utils import to_srt

            self.finished.emit(to_srt(rewritten_segments), "")
        except Exception as exc:
            print(f"Rewrite Thread Error: {exc}")
            self.finished.emit("", str(exc))


class RuntimeAssetsWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, workspace_root, whisper_model_name="base", demucs_model_name="htdemucs"):
        super().__init__()
        self.workspace_root = workspace_root
        self.whisper_model_name = whisper_model_name
        self.demucs_model_name = demucs_model_name

    def run(self):
        try:
            details = []

            ffmpeg_path = Path(self.workspace_root) / "bin" / "ffmpeg" / "ffmpeg.exe"
            if not ffmpeg_path.exists():
                raise FileNotFoundError(f"Bundled FFmpeg is missing: {ffmpeg_path}")
            details.append(f"FFmpeg ready: {ffmpeg_path}")

            mpv_path = Path(self.workspace_root) / "bin" / "mpv" / "libmpv-2.dll"
            if not mpv_path.exists():
                alt_mpv_path = Path(self.workspace_root) / "bin" / "mpv" / "mpv-2.dll"
                if not alt_mpv_path.exists():
                    raise FileNotFoundError(f"Bundled libmpv is missing: {mpv_path}")
                mpv_path = alt_mpv_path
            details.append(f"libmpv ready: {mpv_path}")

            from faster_whisper import WhisperModel

            whisper_cache_dir = Path(self.workspace_root) / "models" / "faster_whisper"
            whisper_cache_dir.mkdir(parents=True, exist_ok=True)
            WhisperModel(
                self.whisper_model_name,
                device="cpu",
                compute_type="int8",
                download_root=str(whisper_cache_dir),
            )
            details.append(f"Whisper model ready: {self.whisper_model_name}")

            from demucs.pretrained import get_model

            get_model(self.demucs_model_name)
            details.append(f"Demucs model ready: {self.demucs_model_name}")

            self.finished.emit("\n".join(details), "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class CloneVoicePreparationWorker(QThread):
    finished = Signal(dict, str)

    def __init__(self, workspace_root, audio_path, whisper_model_path, language="vi"):
        super().__init__()
        self.workspace_root = workspace_root
        self.audio_path = audio_path
        self.whisper_model_path = whisper_model_path
        self.language = language

    def _ffprobe_path(self) -> str:
        return os.path.join(self.workspace_root, "bin", "ffmpeg", "ffprobe.exe")

    def _probe_duration_seconds(self) -> float:
        ffprobe = self._ffprobe_path()
        if not os.path.exists(ffprobe):
            raise FileNotFoundError(f"Bundled ffprobe is missing: {ffprobe}")
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                self.audio_path,
            ],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr or proc.stdout or "ffprobe could not read the clone audio.")
        try:
            return float(str(proc.stdout or "").strip() or "0")
        except ValueError as exc:
            raise RuntimeError("ffprobe returned an invalid duration for the clone audio.") from exc

    def run(self):
        try:
            if not self.audio_path or not os.path.exists(self.audio_path):
                raise FileNotFoundError("Please choose a valid audio file for voice cloning.")

            duration_seconds = self._probe_duration_seconds()
            if duration_seconds < 10.0 or duration_seconds > 40.0:
                raise RuntimeError(
                    f"Clone audio must be between 10 and 40 seconds. Current length: {duration_seconds:.1f}s."
                )

            engine = EngineRuntime()
            segments = engine.transcribe_audio(
                self.audio_path,
                self.whisper_model_path,
                language=self.language or "vi",
            )
            transcript = " ".join(
                str(segment.get("text", "")).strip()
                for segment in (segments or [])
                if str(segment.get("text", "")).strip()
            ).strip()
            if not transcript:
                raise RuntimeError("Whisper could not extract a transcript from the clone audio.")

            self.finished.emit(
                {
                    "audio_path": self.audio_path,
                    "duration_seconds": duration_seconds,
                    "transcript": transcript,
                },
                "",
            )
        except Exception as exc:
            self.finished.emit({}, str(exc))


class PrepareWorkflowWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, workspace_root, video_path, mode, source_language, translator_ai, whisper_model_name):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.mode = mode
        self.source_language = source_language
        self.translator_ai = translator_ai
        self.whisper_model_name = whisper_model_name

    def run(self):
        try:
            runtime = WorkflowRuntime(self.workspace_root)
            state = runtime.run_prepare(
                self.video_path,
                source_language=self.source_language,
                target_language="vi",
                mode=self.mode,
                translator_ai=self.translator_ai,
                whisper_model_name=self.whisper_model_name,
            )
            self.finished.emit(runtime.project_state_path(state), "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class VoiceOverWorker(QThread):
    finished = Signal(str, str, str)

    def __init__(self, workspace_root, segments, output_dir, background_path, voice_name, voice_speed, timing_sync_mode, voice_gain_db, bg_gain_db, project_state_path=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.segments = segments
        self.output_dir = output_dir
        self.background_path = background_path
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.timing_sync_mode = timing_sync_mode
        self.voice_gain_db = voice_gain_db
        self.bg_gain_db = bg_gain_db
        self.project_state_path = project_state_path

    def run(self):
        try:
            runtime = WorkflowRuntime(self.workspace_root)
            result = runtime.run_voice(
                segments=self.segments,
                output_dir=self.output_dir,
                background_path=self.background_path,
                voice_name=self.voice_name,
                voice_speed=self.voice_speed,
                timing_sync_mode=self.timing_sync_mode,
                voice_gain_db=self.voice_gain_db,
                bg_gain_db=self.bg_gain_db,
                project_state_path=self.project_state_path,
            )
            self.finished.emit(result.get("voice_track", ""), result.get("mixed_path", ""), "")
        except Exception as exc:
            self.finished.emit("", "", str(exc))


class FinalExportWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, workspace_root, video_path, output_path, mode, srt_path="", ass_path="", audio_path="", subtitle_style=None, project_state_path=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.ass_path = ass_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}
        self.project_state_path = project_state_path

    def run(self):
        try:
            runtime = WorkflowRuntime(self.workspace_root)
            output = runtime.run_export(
                video_path=self.video_path,
                output_path=self.output_path,
                mode=self.mode,
                srt_path=self.srt_path,
                ass_path=self.ass_path,
                audio_path=self.audio_path,
                subtitle_style=self.subtitle_style,
                project_state_path=self.project_state_path,
            )
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class SegmentAudioPreviewWorker(QThread):
    finished = Signal(int, str, str)

    def __init__(self, workspace_root, index, text, voice_name, voice_speed):
        super().__init__()
        self.workspace_root = workspace_root
        self.index = index
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed

    def run(self):
        try:
            temp_dir = os.path.join(self.workspace_root, "temp", "segment_audio_preview")
            os.makedirs(temp_dir, exist_ok=True)
            wav_path = os.path.join(temp_dir, f"segment_{self.index}_{os.getpid()}.wav")
            engine = EngineRuntime()
            output = engine.synthesize_segment(
                text=self.text,
                wav_path=wav_path,
                voice=self.voice_name,
                speed=self.voice_speed,
                tmp_dir=temp_dir,
            )
            self.finished.emit(self.index, output, "")
        except Exception as exc:
            self.finished.emit(self.index, "", str(exc))


class VoiceSamplePreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, workspace_root, text, voice_name, voice_speed):
        super().__init__()
        self.workspace_root = workspace_root
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed

    def run(self):
        try:
            temp_dir = os.path.join(self.workspace_root, "temp", "voice_sample_preview")
            os.makedirs(temp_dir, exist_ok=True)
            wav_path = os.path.join(temp_dir, f"voice_sample_{os.getpid()}.wav")
            engine = EngineRuntime()
            output = engine.synthesize_segment(
                text=self.text,
                wav_path=wav_path,
                voice=self.voice_name,
                speed=self.voice_speed,
                tmp_dir=temp_dir,
            )
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))
