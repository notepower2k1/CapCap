from __future__ import annotations

import os

import numpy as np

from runtime_paths import find_resource_file, models_path


class SpeakerDiarizationService:
    """Offline speaker-turn extraction using Sherpa-ONNX only.

    The service intentionally uses the CPU provider by default.  Diarization
    is an optional timeline aid, so reserving the GPU for ASR/playback keeps
    the editor responsive.  A compatible Sherpa CUDA build can be selected
    explicitly through SPEAKER_DIARIZATION_PROVIDER=cuda.
    """

    # Keep compatibility with the model bundle already distributed as
    # models/pyannote. The ONNX files are used by Sherpa directly; this does
    # not import pyannote.audio or PyTorch.
    DEFAULT_MODEL_DIR = "pyannote"
    DEFAULT_EMBEDDING_MODEL = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"

    @classmethod
    def _model_path(cls, env_name: str, filename: str) -> str:
        configured = str(os.getenv(env_name, "") or "").strip()
        if configured:
            return os.path.abspath(configured)
        # The Sherpa-ONNX archives carry their own folder, so accept the model
        # whether it sits directly in models/pyannote or one level below.
        located = find_resource_file(models_path(cls.DEFAULT_MODEL_DIR), filename)
        return located or models_path(cls.DEFAULT_MODEL_DIR, filename)

    @classmethod
    def model_paths(cls) -> tuple[str, str]:
        return (
            cls._model_path("SPEAKER_DIARIZATION_SEGMENTATION_MODEL", "model.int8.onnx"),
            cls._model_path("SPEAKER_DIARIZATION_EMBEDDING_MODEL", cls.DEFAULT_EMBEDDING_MODEL),
        )

    @staticmethod
    def _provider() -> str:
        # The installed sherpa-onnx wheel is CPU-only. Keep CPU as a safe
        # default even in GPU mode; CUDA is opt-in for a matching Sherpa build.
        return str(os.getenv("SPEAKER_DIARIZATION_PROVIDER", "cpu") or "cpu").strip().lower()

    @staticmethod
    def _num_threads() -> int:
        try:
            return max(1, min(4, int(os.getenv("SPEAKER_DIARIZATION_THREADS", "2"))))
        except (TypeError, ValueError):
            return 2

    @classmethod
    def cache_key(cls, *, num_speakers: int = -1) -> str:
        segmentation, embedding = cls.model_paths()
        try:
            num_speakers = int(num_speakers)
        except (TypeError, ValueError):
            num_speakers = -1
        values = [
            cls._provider(),
            str(cls._num_threads()),
            os.getenv("SPEAKER_DIARIZATION_CLUSTER_THRESHOLD", "0.9"),
            str(num_speakers if num_speakers >= 2 else -1),
        ]
        for path in (segmentation, embedding):
            try:
                stat = os.stat(path)
                values.append(f"{os.path.abspath(path)}|{stat.st_size}|{stat.st_mtime_ns}")
            except OSError:
                values.append(os.path.abspath(path))
        return "|".join(values)

    def diarize(self, audio_path: str, *, num_speakers: int = -1, progress_callback=None) -> list[dict]:
        if not audio_path or not os.path.isfile(audio_path):
            raise RuntimeError("Speaker diarization needs an extracted audio file.")
        try:
            import sherpa_onnx
        except ImportError as exc:
            raise RuntimeError("sherpa-onnx is not installed. Run: pip install sherpa-onnx") from exc

        segmentation_model, embedding_model = self.model_paths()
        missing = [path for path in (segmentation_model, embedding_model) if not os.path.isfile(path)]
        if missing:
            raise RuntimeError(
                "Speaker diarization models are missing. Install the Sherpa-ONNX segmentation "
                "and speaker-embedding models under models/pyannote. Missing: "
                + ", ".join(missing)
            )

        try:
            threshold = float(os.getenv("SPEAKER_DIARIZATION_CLUSTER_THRESHOLD", "0.9"))
        except (TypeError, ValueError):
            threshold = 0.9
        provider = self._provider()
        threads = self._num_threads()
        try:
            num_speakers = int(num_speakers)
        except (TypeError, ValueError):
            num_speakers = -1

        segmentation = sherpa_onnx.OfflineSpeakerSegmentationModelConfig()
        segmentation.pyannote.model = segmentation_model
        segmentation.provider = provider
        segmentation.num_threads = threads

        embedding = sherpa_onnx.SpeakerEmbeddingExtractorConfig()
        embedding.model = embedding_model
        embedding.provider = provider
        embedding.num_threads = threads

        clustering = sherpa_onnx.FastClusteringConfig(
            num_clusters=num_speakers if num_speakers >= 2 else -1,
            threshold=threshold,
        )
        config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=segmentation,
            embedding=embedding,
            clustering=clustering,
            min_duration_on=0.3,
            min_duration_off=0.5,
        )
        if not config.validate():
            raise RuntimeError(
                "Invalid Sherpa-ONNX diarization configuration. "
                "If CUDA was selected, install a matching CUDA-enabled sherpa-onnx build."
            )

        audio, sample_rate = self._load_audio(audio_path)
        diarizer = sherpa_onnx.OfflineSpeakerDiarization(config)
        if sample_rate != diarizer.sample_rate:
            audio = self._resample(audio, sample_rate, diarizer.sample_rate)
        if not len(audio):
            return []

        def _progress(done: int, total: int) -> int:
            if progress_callback and total:
                progress_callback(done, total)
            return 0

        result = diarizer.process(audio, callback=_progress).sort_by_start_time()
        turns = [
            {"start": max(0.0, float(item.start)), "end": max(0.0, float(item.end)), "speaker": f"SPEAKER_{int(item.speaker):02d}"}
            for item in result
            if float(item.end) - float(item.start) >= 0.05
        ]
        return turns

    @staticmethod
    def _load_audio(audio_path: str) -> tuple[np.ndarray, int]:
        import soundfile as sf

        audio, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        return np.ascontiguousarray(audio[:, 0], dtype=np.float32), int(sample_rate)

    @staticmethod
    def _resample(audio: np.ndarray, sample_rate: int, target_sample_rate: int) -> np.ndarray:
        from scipy.signal import resample_poly

        divisor = int(np.gcd(sample_rate, target_sample_rate))
        return np.ascontiguousarray(
            resample_poly(audio, target_sample_rate // divisor, sample_rate // divisor), dtype=np.float32
        )
