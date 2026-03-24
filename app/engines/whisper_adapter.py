from whisper_processor import transcribe_audio


class WhisperAdapter:
    def transcribe(self, audio_path: str, model_path: str, *, language: str = "auto", task: str = "transcribe"):
        return transcribe_audio(audio_path, model_path, language=language, task=task)
