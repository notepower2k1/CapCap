from vocal_processor import separate_vocals


class DemucsAdapter:
    def separate(self, audio_path: str, output_dir: str):
        return separate_vocals(audio_path, output_dir)
