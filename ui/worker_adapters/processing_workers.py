import os
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import QThread, Signal

APP_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "app")
if APP_PATH not in sys.path:
    sys.path.append(APP_PATH)

from runtime_paths import bin_path
from services import EngineRuntime, ResourceDownloadService, WorkflowRuntime


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
    finished = Signal(list, str)

    def __init__(self, audio_path, model_path, language):
        super().__init__()
        self.audio_path = audio_path
        self.model_path = model_path
        self.language = language

    def run(self):
        try:
            engine = EngineRuntime()
            segments = engine.transcribe_audio(self.audio_path, self.model_path, language=self.language)
            self.finished.emit(segments if segments else [], "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"Transcription Thread Error:\n{details}")
            self.finished.emit([], details or str(exc))


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
    progress = Signal(int, str)  # percent (0-100) or -1 for indeterminate, message

    def __init__(self, workspace_root, whisper_model_name="base", demucs_model_name="htdemucs"):
        super().__init__()
        self.workspace_root = workspace_root
        self.whisper_model_name = whisper_model_name
        self.demucs_model_name = demucs_model_name

    def run(self):
        try:
            details = []

            self.progress.emit(5, "Checking bundled runtime assets...")
            ffmpeg_path = Path(bin_path("ffmpeg", "ffmpeg.exe"))
            if not ffmpeg_path.exists():
                raise FileNotFoundError(f"Bundled FFmpeg is missing: {ffmpeg_path}")
            details.append(f"FFmpeg ready: {ffmpeg_path}")
            self.progress.emit(12, "FFmpeg is ready.")

            mpv_path = Path(bin_path("mpv", "libmpv-2.dll"))
            if not mpv_path.exists():
                alt_mpv_path = Path(bin_path("mpv", "mpv-2.dll"))
                if not alt_mpv_path.exists():
                    raise FileNotFoundError(f"Bundled libmpv is missing: {mpv_path}")
                mpv_path = alt_mpv_path
            details.append(f"libmpv ready: {mpv_path}")
            self.progress.emit(20, "Preview runtime is ready.")

            from whisper_processor import load_whisper_model

            whisper_cache_dir = Path(self.workspace_root) / "models" / "faster_whisper"
            whisper_cache_dir.mkdir(parents=True, exist_ok=True)
            cached = any(
                p.is_dir() and self.whisper_model_name in p.name.lower()
                for p in whisper_cache_dir.glob("models--*")
            )
            if cached:
                self.progress.emit(35, f"Loading Whisper model: {self.whisper_model_name} ...")
            else:
                self.progress.emit(-1, f"Downloading Whisper model: {self.whisper_model_name} ...")
            load_whisper_model(self.whisper_model_name)
            details.append(f"Whisper model ready: {self.whisper_model_name}")

            self.progress.emit(80, f"Whisper model ready: {self.whisper_model_name}")
            from demucs.pretrained import get_model

            try:
                self.progress.emit(-1, f"Downloading Demucs model: {self.demucs_model_name} ...")
                get_model(self.demucs_model_name)
                details.append(f"Demucs model ready: {self.demucs_model_name}")
            except Exception as demucs_exc:
                warning = f"Demucs preload skipped: {demucs_exc}"
                print(f"RuntimeAssetsWorker Warning: {warning}")
                details.append(warning)

            self.progress.emit(100, "All models ready.")
            self.finished.emit("\n".join(details), "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"RuntimeAssetsWorker Error:\n{details}")
            self.finished.emit("", details or str(exc))


class ResourceDownloadWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(int, str)

    def __init__(self, workspace_root, resource_id):
        super().__init__()
        self.workspace_root = workspace_root
        self.resource_id = resource_id

    def run(self):
        try:
            service = ResourceDownloadService(self.workspace_root)
            service.download_resource(self.resource_id, progress_cb=self.progress.emit)
            self.finished.emit(self.resource_id, "")
        except Exception as exc:
            details = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)).strip()
            print(f"ResourceDownloadWorker Error:\n{details}")
            self.finished.emit(self.resource_id, details or str(exc))


class PrepareWorkflowWorker(QThread):
    finished = Signal(str, str)
    step_started = Signal(str)

    def __init__(
        self,
        workspace_root,
        video_path,
        mode,
        audio_handling_mode,
        source_language,
        translator_ai,
        optimize_subtitles,
        translator_style,
        whisper_model_name,
    ):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.mode = mode
        self.audio_handling_mode = audio_handling_mode
        self.source_language = source_language
        self.translator_ai = translator_ai
        self.optimize_subtitles = optimize_subtitles
        self.translator_style = translator_style
        self.whisper_model_name = whisper_model_name

    def run(self):
        try:
            runtime = WorkflowRuntime(self.workspace_root)
            state = runtime.run_prepare(
                self.video_path,
                source_language=self.source_language,
                target_language="vi",
                mode=self.mode,
                audio_handling_mode=self.audio_handling_mode,
                translator_ai=self.translator_ai,
                optimize_subtitles=self.optimize_subtitles,
                translator_style=self.translator_style,
                whisper_model_name=self.whisper_model_name,
                step_callback=lambda s: self.step_started.emit(s)
            )
            self.finished.emit(runtime.project_state_path(state), "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class VoiceOverWorker(QThread):
    finished = Signal(str, str, object, str)
    progress = Signal(str)  # New signal for progress messages

    def __init__(self, workspace_root, segments, output_dir, background_path, audio_handling_mode, voice_name, voice_speed, timing_sync_mode, voice_gain_db, bg_gain_db, ducking_amount_db, project_state_path="", project_temp_dir="", ai_rewrite_dubbing=False, dubbing_style_instruction="", source_language="auto"):
        super().__init__()
        self.workspace_root = workspace_root
        self.segments = segments
        self.output_dir = output_dir
        self.background_path = background_path
        self.audio_handling_mode = audio_handling_mode
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.timing_sync_mode = timing_sync_mode
        self.voice_gain_db = voice_gain_db
        self.bg_gain_db = bg_gain_db
        self.ducking_amount_db = ducking_amount_db
        self.project_state_path = project_state_path
        self.project_temp_dir = project_temp_dir
        self.ai_rewrite_dubbing = ai_rewrite_dubbing
        self.dubbing_style_instruction = dubbing_style_instruction
        self.source_language = source_language

    def run(self):
        try:
            print(f"[VoiceOverWorker DEBUG] Starting with voice_name='{self.voice_name}'")
            self.progress.emit(f"[VoiceOverWorker DEBUG] voice_name='{self.voice_name}'")
            
            runtime = WorkflowRuntime(self.workspace_root)
            result = runtime.run_voice(
                segments=self.segments,
                output_dir=self.output_dir,
                background_path=self.background_path,
                audio_handling_mode=self.audio_handling_mode,
                voice_name=self.voice_name,
                voice_speed=self.voice_speed,
                timing_sync_mode=self.timing_sync_mode,
                voice_gain_db=self.voice_gain_db,
                bg_gain_db=self.bg_gain_db,
                ducking_amount_db=self.ducking_amount_db,
                project_state_path=self.project_state_path,
                project_temp_dir=self.project_temp_dir,
                ai_rewrite_dubbing=self.ai_rewrite_dubbing,
                dubbing_style_instruction=self.dubbing_style_instruction,
                source_language=self.source_language,
                on_progress=self.progress.emit,  # Pass callback
            )
            self.finished.emit(
                result.get("voice_track", ""),
                result.get("mixed_path", ""),
                result.get("segments", []),
                "",
            )
        except Exception as exc:
            print(f"[VoiceOverWorker ERROR] {str(exc)}")
            self.finished.emit("", "", [], str(exc))


class FinalExportWorker(QThread):
    finished = Signal(str, str)
    progress = Signal(int, str)

    def __init__(self, workspace_root, video_path, output_path, mode, srt_path="", ass_path="", audio_path="", subtitle_style=None, output_quality="source", output_fps="source", output_ratio="source", output_scale_mode="fit", output_fill_focus_x=0.5, output_fill_focus_y=0.5, video_filter_state=None, project_state_path="", project_temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.video_path = video_path
        self.output_path = output_path
        self.mode = mode
        self.srt_path = srt_path
        self.ass_path = ass_path
        self.audio_path = audio_path
        self.subtitle_style = subtitle_style or {}
        self.output_quality = output_quality
        self.output_fps = output_fps
        self.output_ratio = output_ratio
        self.output_scale_mode = output_scale_mode
        self.output_fill_focus_x = output_fill_focus_x
        self.output_fill_focus_y = output_fill_focus_y
        self.video_filter_state = video_filter_state or {}
        self.project_state_path = project_state_path
        self.project_temp_dir = project_temp_dir

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
                output_quality=self.output_quality,
                output_fps=self.output_fps,
                output_ratio=self.output_ratio,
                output_scale_mode=self.output_scale_mode,
                output_fill_focus_x=self.output_fill_focus_x,
                output_fill_focus_y=self.output_fill_focus_y,
                video_filter_state=self.video_filter_state,
                project_state_path=self.project_state_path,
                project_temp_dir=self.project_temp_dir,
                on_progress=self.progress.emit,
            )
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))


class SegmentAudioPreviewWorker(QThread):
    finished = Signal(int, str, str)

    def __init__(self, workspace_root, index, text, voice_name, voice_speed, temp_dir="", cache_temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.index = index
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.temp_dir = temp_dir
        self.cache_temp_dir = cache_temp_dir

    def run(self):
        try:
            engine = EngineRuntime()
            preview_temp_dir = self.temp_dir or os.path.join(self.workspace_root, "temp", "segment_audio_preview")
            os.makedirs(preview_temp_dir, exist_ok=True)
            cache_temp_dir = self.cache_temp_dir or preview_temp_dir
            os.makedirs(cache_temp_dir, exist_ok=True)

            from workflows.voice_workflow import VoiceWorkflow

            workflow = VoiceWorkflow(self.workspace_root)
            requested_speed = workflow._clamp_requested_speed(float(self.voice_speed))
            voice_provider = workflow._voice_provider(self.voice_name)
            provider_speed = workflow._provider_native_speed(
                provider=voice_provider,
                requested_speed=requested_speed,
            )
            residual_speed = (requested_speed / provider_speed) if provider_speed > 0.0 else requested_speed

            base_wav_path = os.path.join(cache_temp_dir, f"seg_{self.index:04d}_base.wav")
            engine.synthesize_segment(
                text=self.text,
                wav_path=base_wav_path,
                voice=self.voice_name,
                speed=provider_speed,
                tmp_dir=cache_temp_dir,
            )

            manifest = workflow._load_manifest(cache_temp_dir)
            manifest_segments = dict(manifest.get("segments", {}) or {})
            manifest_by_cache_key = dict(manifest.get("by_cache_key", {}) or {})
            cache_key = workflow._segment_cache_key(
                text=self.text,
                voice_name=self.voice_name,
                provider_speed=provider_speed,
            )
            manifest_entry = {
                "cache_key": cache_key,
                "wav_path": base_wav_path,
                "text": self.text,
                "voice_name": self.voice_name,
                "provider_speed": provider_speed,
            }
            manifest_segments[str(self.index)] = manifest_entry
            manifest_by_cache_key[cache_key] = dict(manifest_entry)
            manifest["segments"] = manifest_segments
            manifest["by_cache_key"] = manifest_by_cache_key
            workflow._save_manifest(cache_temp_dir, manifest)

            wav_path = os.path.join(preview_temp_dir, f"segment_{self.index}_{os.getpid()}.wav")
            if abs(residual_speed - 1.0) >= 0.02:
                output = engine.change_wav_speed(
                    input_wav_path=base_wav_path,
                    output_wav_path=wav_path,
                    speed_ratio=residual_speed,
                )
            else:
                output = base_wav_path
            self.finished.emit(self.index, output, "")
        except Exception as exc:
            self.finished.emit(self.index, "", str(exc))


class VoiceSamplePreviewWorker(QThread):
    finished = Signal(str, str)

    def __init__(self, workspace_root, text, voice_name, voice_speed, temp_dir=""):
        super().__init__()
        self.workspace_root = workspace_root
        self.text = text
        self.voice_name = voice_name
        self.voice_speed = voice_speed
        self.temp_dir = temp_dir

    def run(self):
        try:
            temp_dir = self.temp_dir or os.path.join(self.workspace_root, "temp", "voice_sample_preview")
            os.makedirs(temp_dir, exist_ok=True)
            wav_path = os.path.join(temp_dir, f"voice_sample_{os.getpid()}.wav")
            base_wav_path = os.path.join(temp_dir, f"voice_sample_{os.getpid()}_base.wav")
            engine = EngineRuntime()
            engine.synthesize_segment(
                text=self.text,
                wav_path=base_wav_path,
                voice=self.voice_name,
                speed=1.0,
                tmp_dir=temp_dir,
            )
            speed_value = float(self.voice_speed)
            if abs(speed_value - 1.0) >= 0.02:
                output = engine.change_wav_speed(
                    input_wav_path=base_wav_path,
                    output_wav_path=wav_path,
                    speed_ratio=speed_value,
                )
            else:
                output = base_wav_path
            self.finished.emit(output, "")
        except Exception as exc:
            self.finished.emit("", str(exc))



