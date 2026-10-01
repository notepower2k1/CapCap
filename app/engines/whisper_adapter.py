from whisper_processor import load_whisper_model, transcribe_audio, transcribe_audio_with_model
from services.gpu_stage_scheduler import GPUStageScheduler


class WhisperAdapter:
    def transcribe(self, audio_path: str, model_path: str, *, language: str = "auto", task: str = "transcribe", on_progress=None):
        with GPUStageScheduler.stage("whisper"):
            return transcribe_audio(audio_path, model_path, language=language, task=task, on_progress=on_progress)

    def load_model(self, model_path: str):
        return load_whisper_model(model_path)

    def transcribe_with_model(self, model, audio_path: str, *, language: str = "auto", task: str = "transcribe", use_batched: bool = True, vad_filter: bool = True, on_progress=None):
        with GPUStageScheduler.stage("whisper"):
            return transcribe_audio_with_model(
                model, audio_path, language=language, task=task, use_batched=use_batched, vad_filter=vad_filter, on_progress=on_progress
            )
