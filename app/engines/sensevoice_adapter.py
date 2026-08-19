from sensevoice_processor import transcribe_audio, load_model


class SenseVoiceAdapter:
    def transcribe(
        self,
        audio_path: str,
        model_path: str,
        *,
        language: str = "auto",
        progress_callback=None,
        split_sentences: bool = True,
    ):
        return transcribe_audio(
            audio_path,
            model_path,
            language=language,
            progress_callback=progress_callback,
            split_sentences=split_sentences,
        )

    def load_model(self, model_dir: str, language: str = "auto"):
        return load_model(model_dir, language=language)

